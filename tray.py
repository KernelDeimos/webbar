"""tray.py — a pure-Python StatusNotifier (SNI) system-tray host for webbar.

Implements org.kde.StatusNotifierWatcher + a Host over Gio's DBus (no external deps).
Apps that speak StatusNotifier/AppIndicator register their tray item with us; we fetch
each item's icon + tooltip, decode the icon to a PNG data-URI, and hand the list to a
callback (webbar renders them as HTML in the bar). Left-click calls the item's Activate;
right-click asks for its ContextMenu. Legacy XEmbed-only apps are not covered (rare now).
"""
import base64

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gio, GLib, GdkPixbuf  # noqa: E402

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg type="s" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
  </interface>
</node>
"""
ITEM_IFACE = "org.kde.StatusNotifierItem"


def _argb_to_png_datauri(w, h, data):
    """SNI IconPixmap is ARGB32, network (big-endian) byte order. GdkPixbuf wants RGBA."""
    ba = bytearray(data)
    for i in range(0, len(ba) - 3, 4):
        a, r, g, b = ba[i], ba[i + 1], ba[i + 2], ba[i + 3]
        ba[i], ba[i + 1], ba[i + 2], ba[i + 3] = r, g, b, a
    pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(ba)),
                                         GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)
    ok, buf = pb.save_to_bufferv("png", [], [])
    return "data:image/png;base64," + base64.b64encode(buf).decode()


def _icon_name_datauri(name, size=22):
    """Resolve a themed icon name to a PNG data-URI, best-effort."""
    try:
        from gi.repository import Gtk
        info = Gtk.IconTheme.get_default().lookup_icon(name, size, 0)
        if info:
            pb = info.load_icon()
            ok, buf = pb.save_to_bufferv("png", [], [])
            return "data:image/png;base64," + base64.b64encode(buf).decode()
    except Exception:
        pass
    return None


class TrayHost:
    def __init__(self, on_change):
        self.on_change = on_change      # called with [{service, icon, tooltip}] on any change
        self.items = {}                 # service -> {bus, path, icon, tooltip}
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML)
        self._iface = node.interfaces[0]
        self._own_id = Gio.bus_own_name_on_connection(
            self.bus, "org.kde.StatusNotifierWatcher",
            Gio.BusNameOwnerFlags.NONE, self._on_acquired, None)
        # notice items whose owning process exits
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus",
                                  "NameOwnerChanged", "/org/freedesktop/DBus", None,
                                  Gio.DBusSignalFlags.NONE, self._on_name_owner_changed, None)

    # ---- watcher object ----
    def _on_acquired(self, conn, name):
        # bus_own_name's acquired handler is called with (connection, name).
        conn.register_object("/StatusNotifierWatcher", self._iface,
                             self._method_call, self._get_prop, None)
        # announce a host is present (items wait for this before docking). We ARE the host.
        self._emit("StatusNotifierHostRegistered", None)

    def _method_call(self, conn, sender, path, iface, method, params, inv):
        if method == "RegisterStatusNotifierItem":
            arg = params.unpack()[0]
            # arg is either "bus.name" (path defaults to /StatusNotifierItem) or "bus/path"
            if arg.startswith("/"):
                bus, p = sender, arg
            elif "/" in arg:
                bus, p = arg.split("/", 1); p = "/" + p
            else:
                bus, p = arg, "/StatusNotifierItem"
            service = bus + p
            self._add_item(service, bus, p)
            self._emit("StatusNotifierItemRegistered", GLib.Variant("(s)", (service,)))
            inv.return_value(None)
        elif method == "RegisterStatusNotifierHost":
            self._emit("StatusNotifierHostRegistered", None)
            inv.return_value(None)
        else:
            inv.return_value(None)

    def _get_prop(self, conn, sender, path, iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self.items.keys()))
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def _emit(self, signal, body):
        try:
            self.bus.emit_signal(None, "/StatusNotifierWatcher",
                                 "org.kde.StatusNotifierWatcher", signal, body)
        except Exception:
            pass

    # ---- items ----
    def _add_item(self, service, bus, path):
        if service in self.items:
            return
        self.items[service] = {"bus": bus, "path": path, "icon": None, "tooltip": ""}
        self._refresh_item(service)
        # re-fetch when the item announces a change
        for sig in ("NewIcon", "NewStatus", "NewToolTip", "NewTitle"):
            self.bus.signal_subscribe(bus, ITEM_IFACE, sig, path, None,
                                      Gio.DBusSignalFlags.NONE,
                                      lambda *a, s=service: self._refresh_item(s), None)

    def _refresh_item(self, service):
        it = self.items.get(service)
        if not it:
            return
        try:
            res = self.bus.call_sync(it["bus"], it["path"], "org.freedesktop.DBus.Properties",
                                     "GetAll", GLib.Variant("(s)", (ITEM_IFACE,)),
                                     GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, 2000, None)
            props = res.unpack()[0]
        except Exception:
            return
        icon = None
        pm = props.get("IconPixmap")
        if pm:
            best = max(pm, key=lambda t: t[0] * t[1])   # largest available
            try:
                icon = _argb_to_png_datauri(best[0], best[1], bytes(best[2]))
            except Exception:
                icon = None
        if not icon and props.get("IconName"):
            icon = _icon_name_datauri(props["IconName"])
        tt = props.get("ToolTip")
        tooltip = (tt[2] if isinstance(tt, tuple) and len(tt) > 2 else "") or props.get("Title", "") or ""
        it["icon"] = icon
        it["tooltip"] = tooltip
        self._notify()

    def _on_name_owner_changed(self, conn, sender, path, iface, signal, params, _u):
        name, old, new = params.unpack()
        if old and not new:                       # a bus name vanished → drop its items
            gone = [s for s, it in self.items.items() if it["bus"] == name]
            for s in gone:
                del self.items[s]
                self._emit("StatusNotifierItemUnregistered", GLib.Variant("(s)", (s,)))
            if gone:
                self._notify()

    def _notify(self):
        payload = [{"service": s, "icon": it["icon"], "tooltip": it["tooltip"]}
                   for s, it in self.items.items() if it.get("icon")]
        self.on_change(payload)

    # ---- actions (from the page) ----
    def activate(self, service, x=0, y=0, button="left"):
        it = self.items.get(service)
        if not it:
            return
        method = "Activate" if button == "left" else "ContextMenu"
        try:
            self.bus.call_sync(it["bus"], it["path"], ITEM_IFACE, method,
                               GLib.Variant("(ii)", (int(x), int(y))),
                               None, Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:
            # some items only implement SecondaryActivate / scroll; ignore failures
            pass
