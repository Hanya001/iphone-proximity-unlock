import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


AGENT_XML = """
<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Cancel"/>
</interface></node>
"""


class Bluetooth:
    def __init__(self, confirm, notify):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.confirm = confirm
        self.notify = notify
        self.adapter = None
        self.powered = False
        self.scanning = False
        self.agent_registered = False
        self.pairing_path = None
        self.agent_path = "/io/github/iphone_unlock/agent"
        self.registration = self.bus.register_object(
            self.agent_path, Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0],
            self._agent_call, None, None,
        )

    def _call(self, path, interface, method, parameters, callback, timeout=15000):
        def finished(connection, result):
            try:
                reply = connection.call_finish(result)
            except GLib.Error as error:
                callback(None, error)
            else:
                callback(reply, None)
        self.bus.call("org.bluez", path, interface, method, parameters, None,
                      Gio.DBusCallFlags.NONE, timeout, None, finished)

    def refresh(self, callback):
        def finished(reply, error):
            if error:
                callback([], error)
                return
            objects = reply.unpack()[0]
            adapters = [(path, interfaces["org.bluez.Adapter1"])
                        for path, interfaces in objects.items() if "org.bluez.Adapter1" in interfaces]
            adapters.sort(key=lambda item: not item[1].get("Powered", False))
            if adapters:
                self.adapter, properties = adapters[0]
                self.powered = properties.get("Powered", False)
            else:
                self.adapter = None
                self.powered = False
            devices = []
            for path, interfaces in objects.items():
                properties = interfaces.get("org.bluez.Device1")
                if properties is None or properties.get("Adapter") != self.adapter:
                    continue
                devices.append({
                    "path": path,
                    "address": properties.get("Address", ""),
                    "name": properties.get("Name", properties.get("Alias", "")),
                    "named": bool(properties.get("Name")),
                    "paired": properties.get("Paired", False),
                    "bonded": properties.get("Bonded", False),
                    "connected": properties.get("Connected", False),
                    "rssi": properties.get("RSSI"),
                })
            devices.sort(key=lambda device: (not device["paired"], device["name"].casefold()))
            callback(devices, None)
        self._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None, finished)

    def set_power(self, callback):
        if not self.adapter:
            callback(None, RuntimeError("未找到蓝牙适配器"))
            return
        self._call(self.adapter, "org.freedesktop.DBus.Properties", "Set",
                   GLib.Variant("(ssv)", ("org.bluez.Adapter1", "Powered", GLib.Variant("b", True))), callback)

    def start_scan(self, callback):
        if not self.adapter or not self.powered:
            callback(None, RuntimeError("请先打开电脑蓝牙"))
            return
        adapter = self.adapter
        def finished(reply, error):
            if error is None:
                self.scanning = True
                self.scan_adapter = adapter
            callback(reply, error)
        self._call(adapter, "org.bluez.Adapter1", "StartDiscovery", None, finished)

    def stop_scan(self):
        if self.scanning:
            self.scanning = False
            self._call(self.scan_adapter, "org.bluez.Adapter1", "StopDiscovery", None, lambda reply, error: None)

    def pair(self, device, callback):
        if self.pairing_path:
            callback(None, RuntimeError("另一个配对正在进行"))
            return
        self.pairing_path = device["path"]
        def paired(reply, error):
            self.pairing_path = None
            callback(reply, error)
        def registered(reply, error):
            if error:
                self.pairing_path = None
                callback(None, error)
                return
            self.agent_registered = True
            self._call(device["path"], "org.bluez.Device1", "Pair", None, paired, timeout=120000)
        if self.agent_registered:
            registered(None, None)
        else:
            self._call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent",
                       GLib.Variant("(os)", (self.agent_path, "KeyboardDisplay")), registered)

    def cancel_pair(self):
        if self.pairing_path:
            self._call(self.pairing_path, "org.bluez.Device1", "CancelPairing", None,
                       lambda reply, error: self.notify(f"取消失败：{error}" if error else "配对已取消"))

    def _agent_call(self, connection, sender, object_path, interface, method, parameters, invocation):
        def owner_checked(bus, result):
            try:
                owner = bus.call_finish(result).unpack()[0]
            except GLib.Error:
                owner = None
            if sender != owner:
                invocation.return_dbus_error("org.bluez.Error.Rejected", "Unexpected sender")
                return
            self._handle_agent(method, parameters.unpack(), invocation)
        self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "GetNameOwner", GLib.Variant("(s)", ("org.bluez",)), GLib.VariantType.new("(s)"),
                      Gio.DBusCallFlags.NONE, 3000, None, owner_checked)

    def _handle_agent(self, method, values, invocation):
        if method in {"Release", "Cancel"}:
            self.notify("配对已结束" if method == "Release" else "配对已取消")
            invocation.return_value(None)
            return
        if not values or values[0] != self.pairing_path:
            invocation.return_dbus_error("org.bluez.Error.Rejected", "Not the selected device")
            return
        if method in {"DisplayPinCode", "DisplayPasskey"}:
            code = str(values[1]) if method == "DisplayPinCode" else f"{values[1]:06d}"
            self.notify(f"请在手机上输入配对码：{code}")
            invocation.return_value(None)
            return
        def respond(accepted, value=""):
            if not accepted:
                invocation.return_dbus_error("org.bluez.Error.Rejected", "Pairing rejected by user")
            elif method == "RequestPinCode":
                invocation.return_value(GLib.Variant("(s)", (value,)))
            elif method == "RequestPasskey":
                if not value.isascii() or not value.isdigit() or len(value) > 6:
                    invocation.return_dbus_error("org.bluez.Error.Rejected", "Invalid passkey")
                else:
                    invocation.return_value(GLib.Variant("(u)", (int(value),)))
            else:
                invocation.return_value(None)
        code = f"{values[1]:06d}" if method == "RequestConfirmation" else ""
        self.confirm(method, code, respond)

    def close(self):
        self.stop_scan()
        if self.pairing_path:
            self._call(self.pairing_path, "org.bluez.Device1", "CancelPairing", None, lambda reply, error: None)
        if self.agent_registered:
            self._call("/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent",
                       GLib.Variant("(o)", (self.agent_path,)), lambda reply, error: None)
        self.bus.unregister_object(self.registration)
