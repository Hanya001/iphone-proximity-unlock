import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {UnlockDialog} from 'resource:///org/gnome/shell/ui/unlockDialog.js';

export default class IPhoneUnlock extends Extension {
    enable() {
        this._enabled = true;
        this._process = null;
        this._lastRequest = 0;
        this._panels = new Set();
        this._injections = new InjectionManager();
        const extension = this;
        this._injections.overrideMethod(UnlockDialog.prototype, '_init', original => {
            return function (...args) {
                original.call(this, ...args);
                extension._addButtons(this);
            };
        });
        this._injections.overrideMethod(UnlockDialog.prototype, '_ensureAuthPrompt', original => {
            return function (...args) {
                original.call(this, ...args);
                for (const panel of extension._panels) {
                    if (panel.host === this._promptBox)
                        panel.host.set_child_above_sibling(panel.container, null);
                }
            };
        });
        if (Main.sessionMode.isLocked && Main.screenShield._dialog)
            this._addButtons(Main.screenShield._dialog);
        this._eventId = global.stage.connect('captured-event', (_stage, event) => {
            if (!Main.sessionMode.isLocked || Main.sessionMode.isGreeter)
                return Clutter.EVENT_PROPAGATE;
            if (event.type() !== Clutter.EventType.KEY_PRESS)
                return Clutter.EVENT_PROPAGATE;
            const modifiers = event.get_state();
            const required = Clutter.ModifierType.CONTROL_MASK | Clutter.ModifierType.MOD1_MASK;
            const symbol = event.get_key_symbol();
            if ((modifiers & required) !== required ||
                (symbol !== Clutter.KEY_u && symbol !== Clutter.KEY_U))
                return Clutter.EVENT_PROPAGATE;
            this._requestUnlock();
            return Clutter.EVENT_STOP;
        });
        console.log('iPhone Unlock: lock screen buttons and Ctrl+Alt+U enabled');
    }

    _addButtons(dialog) {
        for (const host of [dialog._clock, dialog._promptBox]) {
            if (!host || [...this._panels].some(panel => panel.host === host))
                continue;
            const container = new St.BoxLayout({
                orientation: Clutter.Orientation.VERTICAL,
                x_align: Clutter.ActorAlign.CENTER,
                style: 'margin-top: 24px; spacing: 8px;',
            });
            const button = new St.Button({
                label: '用 iPhone 解锁',
                accessible_name: '用附近的 iPhone 验证并解锁',
                style_class: 'button',
                style: 'min-width: 180px; padding: 12px 24px;',
                x_align: Clutter.ActorAlign.CENTER,
                can_focus: true,
                reactive: true,
            });
            const status = new St.Label({
                text: '点击验证附近的 iPhone',
                style: 'font-size: 12px; color: rgba(255,255,255,0.8);',
                x_align: Clutter.ActorAlign.CENTER,
            });
            const panel = {host, container, button, status};
            button.connect('clicked', () => this._requestUnlock());
            container.connect('destroy', () => this._panels.delete(panel));
            container.add_child(button);
            container.add_child(status);
            host.add_child(container);
            this._panels.add(panel);
        }
    }

    _setStatus(message, busy = false) {
        for (const panel of this._panels) {
            panel.button.label = busy ? '正在验证…' : '用 iPhone 解锁';
            panel.button.reactive = !busy;
            panel.button.can_focus = !busy;
            panel.status.text = message;
        }
    }

    _requestUnlock() {
        if (!this._enabled || !Main.sessionMode.isLocked || Main.sessionMode.isGreeter)
            return;
        const now = GLib.get_monotonic_time();
        if (this._process || now - this._lastRequest < 2_000_000)
            return;
        this._lastRequest = now;
        this._setStatus('正在检查手机连接和信号…', true);
        const helper = GLib.build_filenamev([
            GLib.get_home_dir(), '.local', 'lib', 'iphone-proximity-unlock', 'watcher.py',
        ]);
        try {
            const process = Gio.Subprocess.new([
                '/usr/bin/python3', helper, '--request',
            ], Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            this._process = process;
            process.communicate_utf8_async(null, null, (_source, result) => {
                try {
                    const [, , errors] = process.communicate_utf8_finish(result);
                    console.log(`iPhone Unlock: ${errors.trim()}`);
                    if (this._enabled) {
                        this._setStatus(process.get_successful()
                            ? '验证通过，正在解锁…'
                            : '未通过验证，请靠近手机或使用密码');
                    }
                } catch (error) {
                    console.error(`iPhone Unlock: ${error.message}`);
                    if (this._enabled)
                        this._setStatus('验证失败，请重试或使用密码');
                } finally {
                    if (this._process === process)
                        this._process = null;
                }
            });
        } catch (error) {
            console.error(`iPhone Unlock: ${error.message}`);
            this._setStatus('无法启动验证，请使用密码');
        }
    }

    disable() {
        this._enabled = false;
        this._injections.clear();
        this._injections = null;
        for (const panel of this._panels)
            panel.container.destroy();
        this._panels.clear();
        if (this._eventId) {
            global.stage.disconnect(this._eventId);
            this._eventId = 0;
        }
        if (this._process) {
            this._process.force_exit();
            this._process = null;
        }
    }
}
