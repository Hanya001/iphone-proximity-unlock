import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from bluez import Bluetooth
from config import DEFAULTS, config_path, load_config, save_config
from watcher import command, phone_link, signal_strength


APP_ID = "io.github.iphone_unlock.Settings"


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="iPhone Unlock", default_width=680, default_height=760)
        self.closed = False
        self.devices = []
        self.busy = False
        self.pairing = False
        self.refreshing = False
        self.updating = False
        self.signal_busy = False
        self.last_signal = None
        self.last_signal_address = None
        self.scan_timer = 0
        self.confirm_dialog = None
        self.confirm_reply = None
        self.executor = ThreadPoolExecutor(max_workers=2)
        initial_error = None
        try:
            self.settings = load_config()
        except (OSError, ValueError) as error:
            self.settings = DEFAULTS.copy()
            initial_error = f"配置读取失败，请检查后重新保存：{error}"

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.save_button = Gtk.Button(label="保存设置", css_classes=["suggested-action"])
        self.save_button.connect("clicked", self._save)
        header.pack_end(self.save_button)
        toolbar.add_top_bar(header)
        self.overlay = Adw.ToastOverlay()
        self.page = Adw.PreferencesPage(title="设置", icon_name="bluetooth-active-symbolic")
        self.overlay.set_child(self.page)
        toolbar.set_content(self.overlay)
        self.set_content(toolbar)

        general = Adw.PreferencesGroup(title="手动验证，快速解锁", description="锁屏后点击“用 iPhone 解锁”，或按 Ctrl + Alt + U。手机靠近不会自行解锁。")
        self.enabled_row = Adw.SwitchRow(title="允许 iPhone 解锁", subtitle="修改后点击右上角保存；关闭后仍可使用密码")
        self.enabled_row.set_active(self.settings["enabled"])
        general.add(self.enabled_row)
        self.auto_connect_row = Adw.SwitchRow(title="允许主动重连", subtitle="使用非音频连接；关闭时只检测已有连接")
        self.auto_connect_row.set_active(self.settings["auto_connect"])
        self.auto_connect_row.connect("notify::active", lambda *_args: self._update_test_mode())
        general.add(self.auto_connect_row)
        self.page.add(general)

        device_group = Adw.PreferencesGroup(title="1 · 选择并配对手机", description="让 iPhone 停留在“设置 → 蓝牙”页面，再扫描设备。")
        self.adapter_row = Adw.ActionRow(title="电脑蓝牙", subtitle="正在检查…")
        self.power_button = Gtk.Button(label="打开蓝牙", valign=Gtk.Align.CENTER)
        self.power_button.connect("clicked", self._power_on)
        self.adapter_row.add_suffix(self.power_button)
        device_group.add(self.adapter_row)
        self.device_model = Gtk.StringList.new(["正在读取设备…"])
        self.device_row = Adw.ComboRow(title="目标设备", model=self.device_model)
        self.device_row.connect("notify::selected", self._selected)
        device_group.add(self.device_row)
        self.device_status = Adw.ActionRow(title="设备状态", subtitle="请选择设备")
        controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.scan_button = Gtk.Button(label="扫描设备")
        self.scan_button.connect("clicked", self._scan)
        self.pair_button = Gtk.Button(label="配对")
        self.pair_button.connect("clicked", self._pair)
        controls.append(self.scan_button)
        controls.append(self.pair_button)
        self.device_status.add_suffix(controls)
        device_group.add(self.device_status)
        self.page.add(device_group)

        signal_group = Adw.PreferencesGroup(title="2 · 调整信号门槛", description="先将手机放在平时使用的位置，连接后观察信号。数值越大要求越近；调小则更宽松。")
        self.threshold_row = Adw.SpinRow.new_with_range(-127, 126, 1)
        self.threshold_row.set_title("最低 RSSI")
        self.threshold_row.set_subtitle("只读取一次，不重复等待；默认 -45")
        self.threshold_row.set_value(self.settings["near_threshold"])
        self.threshold_row.connect("notify::value", lambda *_args: self._show_signal())
        signal_group.add(self.threshold_row)
        self.signal_row = Adw.ActionRow(title="实时信号", subtitle="选择已配对的设备后连接测试")
        self.test_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self._update_test_mode()
        self.test_button.connect("clicked", self._test)
        self.signal_row.add_suffix(self.test_button)
        signal_group.add(self.signal_row)
        self.page.add(signal_group)

        integration = Adw.PreferencesGroup(title="3 · 锁屏按钮", description="首次安装或更新扩展后，需要保存工作并注销、重新登录。修改手机和阈值后，保存即生效。")
        self.extension_row = Adw.ActionRow(title="GNOME 扩展", subtitle="正在检查…")
        refresh_button = Gtk.Button(label="刷新状态", valign=Gtk.Align.CENTER)
        refresh_button.connect("clicked", lambda *_args: self._extension_status())
        self.extension_row.add_suffix(refresh_button)
        integration.add(self.extension_row)
        integration.add(Adw.ActionRow(title="使用范围", subtitle="仅解锁已登录的桌面；保留密码，不影响开机登录或磁盘解密"))
        integration.add(Adw.ActionRow(title="注意", subtitle="信号只用于粗略判断远近。手机在附近时，其他人也可能点击按钮解锁。"))
        self.page.add(integration)
        self.status_row = Adw.ActionRow(title="准备就绪", subtitle=f"配置：{config_path()}")
        status_group = Adw.PreferencesGroup()
        status_group.add(self.status_row)
        self.page.add(status_group)
        self.connect("close-request", self._close)

        try:
            self.bluetooth = Bluetooth(self._confirm_pairing, self._notify)
        except GLib.Error as error:
            self.bluetooth = None
            self._notify(f"无法访问蓝牙服务：{error.message}")
        self._update_actions()
        self._refresh()
        self._extension_status()
        self.poll_timer = GLib.timeout_add_seconds(3, self._poll)
        if initial_error:
            self._notify(initial_error)

    def _notify(self, message):
        if self.closed:
            return
        self.status_row.set_title(message)
        self.overlay.add_toast(Adw.Toast.new(message))

    def _task(self, work, finished):
        future = self.executor.submit(work)
        def completed(result):
            try:
                value, error = result.result(), None
            except Exception as exception:
                value, error = None, exception
            def deliver():
                if not self.closed:
                    finished(value, error)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)
        future.add_done_callback(completed)

    def _current(self):
        selected = self.device_row.get_selected()
        return self.devices[selected] if selected < len(self.devices) else None

    def _set_busy(self, busy):
        self.busy = busy
        self._update_actions()

    def _update_test_mode(self):
        if hasattr(self, "test_button"):
            self.test_button.set_label("连接并测试" if self.auto_connect_row.get_active() else "测试已有连接")
        if hasattr(self, "signal_row"):
            self._show_signal()

    def _update_actions(self):
        device = self._current()
        self.save_button.set_sensitive(not self.busy)
        self.scan_button.set_sensitive(not self.busy and self.bluetooth is not None)
        self.pair_button.set_label("取消配对" if self.pairing else "配对")
        self.pair_button.set_sensitive(self.pairing or (not self.busy and device is not None and not device["bonded"]))
        self.test_button.set_sensitive(not self.busy and device is not None and device["paired"] and device["bonded"])
        self.device_row.set_sensitive(not self.busy)

    def _refresh(self):
        if self.closed or self.refreshing or self.bluetooth is None:
            return
        self.refreshing = True
        def finished(devices, error):
            self.refreshing = False
            if self.closed:
                return
            if error:
                self.adapter_row.set_subtitle("无法读取蓝牙设备")
                self._notify(str(error))
                return
            current = self._current()
            selected_address = current["address"] if current else self.settings["device_address"]
            self.devices = [device for device in devices if device["named"] or device["paired"] or device["address"] == selected_address]
            labels = [f'{device["name"]} · {device["address"]}' for device in self.devices] or ["未发现有名称的设备，请打开手机蓝牙设置后扫描"]
            self.updating = True
            self.device_model.splice(0, self.device_model.get_n_items(), labels)
            index = next((index for index, device in enumerate(self.devices) if device["address"] == selected_address), 0)
            self.device_row.set_selected(index)
            self.updating = False
            self.adapter_row.set_subtitle("已开启" if self.bluetooth.powered else "未开启或没有适配器")
            self.power_button.set_visible(bool(self.bluetooth.adapter) and not self.bluetooth.powered)
            self._selected()
        self.bluetooth.refresh(finished)

    def _selected(self, *_args):
        if self.updating:
            return
        device = self._current()
        if device:
            state = "已配对" if device["paired"] and device["bonded"] else "尚未配对"
            self.device_status.set_subtitle(f'{state} · {"已连接" if device["connected"] else "未连接"}')
        else:
            self.device_status.set_subtitle("请选择设备")
        if not device or device["address"] != self.last_signal_address:
            self.last_signal = None
        self._show_signal()
        self._update_actions()

    def _show_signal(self):
        if not hasattr(self, "signal_row"):
            return
        if self.last_signal is None:
            self.signal_row.set_subtitle("暂无信号；仅检测模式需要手机已经连接"
                                         if not self.auto_connect_row.get_active()
                                         else "暂无连接信号，请点击连接并测试")
        else:
            threshold = int(self.threshold_row.get_value())
            verdict = "达到门槛" if self.last_signal >= threshold else "低于门槛"
            self.signal_row.set_subtitle(f"RSSI {self.last_signal} · {verdict}（{threshold}）")

    def _poll(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if not self.busy:
            self._refresh()
            self._read_signal()
        return GLib.SOURCE_CONTINUE

    def _read_signal(self):
        device = self._current()
        if not device or not device["connected"] or self.signal_busy:
            if device and not device["connected"]:
                self.last_signal = None
                self._show_signal()
            return
        self.signal_busy = True
        address = device["address"]
        def finished(strength, error):
            self.signal_busy = False
            current = self._current()
            if current and current["address"] == address:
                self.last_signal_address = address
                self.last_signal = None if error else strength
                self._show_signal()
        self._task(lambda: signal_strength(address), finished)

    def _power_on(self, *_args):
        if self.bluetooth:
            self.bluetooth.set_power(lambda reply, error: self._operation_done("电脑蓝牙已打开", error))

    def _scan(self, *_args):
        if self.bluetooth.scanning:
            self._stop_scan()
            return
        self.scan_button.set_sensitive(False)
        def finished(reply, error):
            if self.closed:
                return
            self._update_actions()
            if error:
                self._notify(f"扫描失败：{error}")
                return
            self.scan_button.set_label("停止扫描")
            self._notify("扫描中，请让 iPhone 停留在蓝牙设置页面")
            self.scan_timer = GLib.timeout_add_seconds(25, self._stop_scan)
            self._refresh()
        self.bluetooth.start_scan(finished)

    def _stop_scan(self):
        if self.scan_timer:
            GLib.source_remove(self.scan_timer)
            self.scan_timer = 0
        self.bluetooth.stop_scan()
        self.scan_button.set_label("扫描设备")
        self._refresh()
        return GLib.SOURCE_REMOVE

    def _pair(self, *_args):
        if self.pairing:
            self.bluetooth.cancel_pair()
            return
        device = self._current()
        if not device:
            return
        self.pairing = True
        self._set_busy(True)
        self._notify("正在配对，请留意电脑和手机上的确认码")
        self.bluetooth.pair(device, lambda reply, error: self._operation_done("配对成功，请连接测试后保存设置", error))

    def _confirm_pairing(self, method, code, respond):
        if self.closed:
            respond(False)
            return
        if self.confirm_dialog:
            respond(False)
            return
        entry = None
        if method == "RequestConfirmation":
            body = f"请确认手机显示的配对码也是 {code}，再点击确认。"
        elif method in {"RequestPinCode", "RequestPasskey"}:
            body = "请输入手机显示的配对码。"
            entry = Gtk.Entry(placeholder_text="配对码", activates_default=True)
        else:
            body = "是否允许为当前选择的手机完成蓝牙配对？"
        dialog = Adw.MessageDialog.new(self, "确认蓝牙配对", body)
        dialog.add_response("cancel", "取消")
        dialog.add_response("confirm", "确认配对")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        if entry:
            dialog.set_extra_child(entry)
        self.confirm_dialog = dialog
        self.confirm_reply = respond
        def response(_dialog, response_id):
            self.confirm_dialog = None
            callback, self.confirm_reply = self.confirm_reply, None
            if callback:
                callback(response_id == "confirm", entry.get_text() if entry else "")
        dialog.connect("response", response)
        dialog.present()

    def _operation_done(self, message, error):
        if self.closed:
            return
        if self.confirm_dialog:
            self.confirm_dialog.close()
        self.pairing = False
        self._set_busy(False)
        self._notify(f"操作失败：{error}" if error else message)
        self._refresh()

    def _test(self, *_args):
        device = self._current()
        if not device:
            return
        self._set_busy(True)
        auto_connect = self.auto_connect_row.get_active()
        self.signal_row.set_subtitle("正在连接并读取信号…" if auto_connect else "正在检查已有连接…")
        def sample():
            def work():
                with phone_link(device["address"], auto_connect):
                    return signal_strength(device["address"])
            def finished(strength, error):
                self._set_busy(False)
                self.last_signal_address = device["address"]
                self.last_signal = strength
                self._show_signal()
                self._notify(f"测试失败：{error}" if error else "测试完成；只测信号，不会解锁桌面" if strength is not None else "连接成功，但无法读取 RSSI，请确认 bluez/hcitool 可用")
                self._refresh()
            self._task(work, finished)
        sample()

    def _save(self, *_args):
        device = self._current()
        enabled = self.enabled_row.get_active()
        if enabled and (not device or not device["paired"] or not device["bonded"]):
            self._notify("请先选择并配对手机，再启用解锁")
            return
        values = {**self.settings, "enabled": enabled,
                  "auto_connect": self.auto_connect_row.get_active(),
                  "near_threshold": int(self.threshold_row.get_value())}
        if device and device["paired"] and device["bonded"]:
            values.update(device_address=device["address"], device_name=device["name"][:128])
        try:
            self.settings = save_config(values)
        except (OSError, ValueError) as error:
            self._notify(f"保存失败：{error}")
        else:
            self._notify("设置已保存，下次点击锁屏按钮即生效" if enabled else "已关闭 iPhone 解锁，密码仍可使用")

    def _extension_status(self):
        def finished(output, error):
            if output and "State: ACTIVE" in output:
                self.extension_row.set_subtitle("已启用；更新扩展代码后需注销重新登录")
            elif output:
                self.extension_row.set_subtitle("已安装，请启用扩展并注销重新登录")
            else:
                self.extension_row.set_subtitle("尚未加载，请安装后注销并重新登录")
        self._task(lambda: command(["gnome-extensions", "info", "iphone-unlock@local"]), finished)

    def _close(self, *_args):
        if self.closed:
            return False
        self.closed = True
        if self.confirm_reply:
            self.confirm_reply(False)
            self.confirm_reply = None
        if self.confirm_dialog:
            self.confirm_dialog.close()
        GLib.source_remove(self.poll_timer)
        if self.scan_timer:
            GLib.source_remove(self.scan_timer)
        if self.bluetooth:
            self.bluetooth.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        return False


class SettingsApplication(Adw.Application):
    def __init__(self, smoke_test=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.smoke_test = smoke_test

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            window = SettingsWindow(self)
        window.present()
        if self.smoke_test:
            def finish():
                device = window._current()
                print(json.dumps({"mapped": window.get_mapped(), "device_count": len(window.devices),
                                  "selected": device["name"] if device else None,
                                  "threshold": window.threshold_row.get_value()}, ensure_ascii=False))
                window.close()
                return GLib.SOURCE_REMOVE
            GLib.timeout_add_seconds(4, finish)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    raise SystemExit(SettingsApplication(arguments.smoke_test).run([sys.argv[0]]))


if __name__ == "__main__":
    main()
