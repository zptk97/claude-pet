// claude-pet(~/.claude/pet/claude_pet.py)을 GNOME Shell 안에서 돕는다.
//
// 1. 펫을 클릭하면 그 세션의 터미널 창을 앞으로 올린다.
//    Wayland 에서는 다른 프로그램이 남의 창을 올릴 수 없어서 GNOME Shell 안에서 대신 올린다.
//
//      gdbus call --session --dest org.gnome.Shell \
//        --object-path /org/gnome/Shell/Extensions/ClaudePet \
//        --method org.gnome.Shell.Extensions.ClaudePet.ActivateWindow '창 제목'
//
// 2. 펫 메뉴가 열린 동안 화면 어디를 누르든 그 위치를 Clicked 신호로 알린다.
//    펫은 XWayland 로 뜨는데 GNOME 은 XWayland 창이 화면 전체 클릭을 잡지 못하게 막아서
//    (xwayland-allow-grabs), 다른 창을 눌러도 펫 메뉴가 닫히지 않기 때문이다.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const OBJECT_PATH = '/org/gnome/Shell/Extensions/ClaudePet';
const IFACE = `
<node>
  <interface name="org.gnome.Shell.Extensions.ClaudePet">
    <method name="ActivateWindow">
      <arg type="s" direction="in" name="title"/>
      <arg type="i" direction="out" name="matched"/>
    </method>
    <method name="WatchClicks">
      <arg type="b" direction="in" name="enabled"/>
    </method>
    <signal name="Clicked">
      <arg type="i" name="x"/>
      <arg type="i" name="y"/>
    </signal>
  </interface>
</node>`;

const BUTTONS = Clutter.ModifierType.BUTTON1_MASK | Clutter.ModifierType.BUTTON2_MASK |
    Clutter.ModifierType.BUTTON3_MASK;
const WATCH_INTERVAL_MS = 30;
// 펫이 멈춤 요청 없이 꺼져도 계속 돌지 않게 이만큼 지나면 스스로 멈춘다
const WATCH_LIMIT_US = 2 * 60 * 1000 * 1000;

// Claude Code 가 제목 앞에 붙이는 상태 글리프(✳, ◐ 등)와 공백을 뗀다.
// claude-pet 의 petcore.clean_title 과 같은 규칙이어야 제목이 맞는다.
function cleanTitle(title) {
    return (title ?? '').replace(/^[^\p{L}\p{N}]+/u, '').trim();
}

export default class ClaudePetFocusExtension extends Extension {
    enable() {
        this._watchId = 0;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
        this._stopWatch();
        this._dbus?.unexport();
        this._dbus = null;
    }

    // 제목이 같은 창 중 가장 최근에 쓴 창을 올린다 (다른 작업 공간이면 그리로 옮겨 간다).
    // 찾은 창 수를 돌려준다. 0 이면 아무것도 하지 않았다.
    ActivateWindow(title) {
        const wanted = cleanTitle(title);
        if (!wanted)
            return 0;
        const matches = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null)
            .filter(window => cleanTitle(window.get_title()) === wanted);
        if (matches.length > 0)
            Main.activateWindow(matches[0]);
        return matches.length;
    }

    // 켜면 마우스 버튼이 새로 눌릴 때마다 그 위치(화면 좌표)를 Clicked 로 보낸다.
    // 메뉴를 연 오른쪽 버튼이 아직 눌려 있을 수 있어서, 켤 때 눌려 있던 버튼은 놓였다가 다시 눌려야 센다.
    WatchClicks(enabled) {
        this._stopWatch();
        if (!enabled)
            return;
        const started = GLib.get_monotonic_time();
        let wasDown = (global.get_pointer()[2] & BUTTONS) !== 0;
        this._watchId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, WATCH_INTERVAL_MS, () => {
            const [x, y, mods] = global.get_pointer();
            const down = (mods & BUTTONS) !== 0;
            if (down && !wasDown)
                this._dbus?.emit_signal('Clicked', new GLib.Variant('(ii)', [x, y]));
            wasDown = down;
            if (GLib.get_monotonic_time() - started > WATCH_LIMIT_US) {
                this._watchId = 0;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stopWatch() {
        if (this._watchId) {
            GLib.source_remove(this._watchId);
            this._watchId = 0;
        }
    }
}
