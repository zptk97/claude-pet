// claude-pet(~/.claude/pet/claude_pet.py)에서 펫을 클릭하면 그 세션의 터미널 창을 앞으로 올린다.
// Wayland 에서는 다른 프로그램이 남의 창을 올릴 수 없어서 GNOME Shell 안에서 대신 올린다.
//
//   gdbus call --session --dest org.gnome.Shell \
//     --object-path /org/gnome/Shell/Extensions/ClaudePet \
//     --method org.gnome.Shell.Extensions.ClaudePet.ActivateWindow '창 제목'

import Gio from 'gi://Gio';
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
  </interface>
</node>`;

// Claude Code 가 제목 앞에 붙이는 상태 글리프(✳, ◐ 등)와 공백을 뗀다.
// claude-pet 의 petcore.clean_title 과 같은 규칙이어야 제목이 맞는다.
function cleanTitle(title) {
    return (title ?? '').replace(/^[^\p{L}\p{N}]+/u, '').trim();
}

export default class ClaudePetFocusExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
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
}
