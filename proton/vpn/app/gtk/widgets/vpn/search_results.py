"""
Server search results module.


Copyright (c) 2023 Proton AG

This file is part of Proton VPN.

Proton VPN is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Proton VPN is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with ProtonVPN.  If not, see <https://www.gnu.org/licenses/>.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Set, Tuple

from gi.repository import GObject
from proton.vpn.session.servers.logicals import sort_servers_alphabetically_by_country_and_server_name

from proton.vpn import logging
from proton.vpn.app.gtk import Gtk
from proton.vpn.app.gtk.utils.safe_signal_connect import safe_signal_connect

logger = logging.getLogger(__name__)

# We're hittig a bug in the GTK treeview where x11 crashes with BadAlloc
# if we try to display > 1500 rows in certain configurations.
# This avoids the issue by limiting the number of results displayed.
# This is ultimately a better user experience as the whole point of
# the search is to find a specific item.
MAX_SEARCH_RESULTS_PER_SECTION = 100

LOAD_COLOR_CSS_CLASS = "dim-label"


class FilteredListRow(Gtk.ListBoxRow):
    """Single row in the search results list."""

    def __init__(self, name: str, load: str = "", *, selectable: bool, selected_value: Optional[str] = None):
        super().__init__()
        self.selected_value = selected_value
        self.set_activatable(selectable)
        self.set_selectable(selectable)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(2)
        row.set_margin_bottom(2)

        name_label = Gtk.Label(label=name)
        name_label.set_hexpand(True)
        name_label.set_halign(Gtk.Align.START)
        name_label.set_xalign(0)
        row.append(name_label)

        load_label = Gtk.Label(label=load)
        load_label.set_halign(Gtk.Align.END)
        load_label.add_css_class(LOAD_COLOR_CSS_CLASS)
        row.append(load_label)

        self.set_child(row)


class FilteredList(Gtk.ListBox):
    """
    Displays a list of countries, locations and servers in a tree view.
    """

    def __init__(
        self,
        countries: Callable[[Optional[str]], Iterable[Tuple[Optional[str], Optional[int]]]],
        locations: Callable[[Optional[str]], Iterable[Tuple[Optional[str], Optional[int]]]],
        servers: Callable[[Optional[str]], Iterable[Tuple[Optional[str], Optional[int]]]],
    ):
        super().__init__()
        self._countries = countries
        self._locations = locations
        self._servers = servers
        self.set_activate_on_single_click(True)
        self.set_selection_mode(Gtk.SelectionMode.SINGLE)

    def _clear_rows(self):
        child = self.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.remove(child)
            child = next_child

    def _append_header_row(self, section_name: str, count: int, load_header: str):
        self.append(
            FilteredListRow(
                name=f"{section_name} ({count})",
                load=load_header,
                selectable=False,
                selected_value=None,
            )
        )

    def _append_result_row(self, name: str, load: Optional[int]):
        load_string = "" if load is None else f"{load}%"
        self.append(
            FilteredListRow(
                name=name,
                load=load_string,
                selectable=True,
                selected_value=name,
            )
        )

    def _append_overflow_row(self):
        self.append(
            FilteredListRow(
                name="...",
                load="",
                selectable=False,
                selected_value=None,
            )
        )

    def update(self, search_text: Optional[str] = None):
        """Rebuild the view using the search_text as a filter"""
        self._clear_rows()

        sections = (
            ("Countries", self._countries, ""),
            ("Locations", self._locations, ""),
            ("Servers", self._servers, "Server load"),
        )

        for section in sections:
            section_name, section_data, section_load_header = section
            data = list(section_data(search_text))
            if not data:
                continue

            self._append_header_row(section_name, len(data), section_load_header)
            for i, (name, load) in enumerate(data):
                if name is None:
                    continue
                self._append_result_row(name, load)
                if i == MAX_SEARCH_RESULTS_PER_SECTION:
                    self._append_overflow_row()
                    break


class SearchResults(Gtk.ScrolledWindow):
    """Display a filtered view of countries and servers.
    Inside a scroll-able widget.
    """

    def __init__(self, controller) -> None:
        super().__init__()
        self._controller = controller
        self.set_name("search-results")
        self.set_policy(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(False)
        self._container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self._container)
        self.set_property("height-request", 200)

        self._revealer: Optional[Gtk.Revealer] = None

        self._filtered_country_list = FilteredList(self._countries, self._locations, self._servers)
        safe_signal_connect(self._filtered_country_list, "row-activated", self._on_row_activated)

        self._container.append(self._filtered_country_list)

    def set_revealer(self, revealer: Optional[Gtk.Revealer]):
        """Sets revealer for search results"""
        self._revealer = revealer

    def _search_input_exists(  # pylint: disable=too-many-arguments
        self,
        search_text: Optional[str],
        server,
        entry_country_name: bool = False,
        location_name: bool = False,
        server_name: bool = False,
    ) -> bool:
        if entry_country_name:
            return bool(search_text and (search_text in server.entry_country_name.lower()))

        if location_name:
            return bool(search_text and (search_text in server.location.lower()))

        if server_name:
            return bool(search_text and (search_text in server.name.lower()))

        return False

    def _countries(self, search_text: Optional[str] = None) -> Set[Tuple[Optional[str], None]]:
        result: Set[Tuple[Optional[str], None]] = set()
        server_list = self._controller.server_list

        if not server_list:
            return result

        for server in server_list:
            if not server.under_maintenance and self._search_input_exists(search_text, server, entry_country_name=True):
                result.add((server.entry_country_name, None))

        return result

    def _locations(
        self,
        search_text: Optional[str] = None,
    ) -> Iterable[Tuple[Optional[str], Optional[int]]]:
        result = set()
        server_list = self._controller.server_list

        if not server_list:
            yield (None, None)
            return

        for server in server_list:
            if (
                not server.under_maintenance
                and self._search_input_exists(search_text, server, location_name=True)
                and server.location
            ):
                result.add(server.location)

        for location in sorted(result):
            yield (location, None)

    def _servers(
        self,
        search_text: Optional[str] = None,
    ) -> Iterable[Tuple[Optional[str], Optional[int]]]:
        server_list = self._controller.server_list
        if not server_list:
            yield (None, None)
            return

        user_tier = self._controller.user_tier
        matches = [
            server
            for server in server_list
            if not server.under_maintenance
            and server.tier <= user_tier
            and self._search_input_exists(search_text, server, server_name=True)
        ]

        for server in sorted(matches, key=sort_servers_alphabetically_by_country_and_server_name):
            yield (server.name, server.load)

    @GObject.Signal(name="result-chosen", arg_types=(str,))
    def result_chosen(self, _row: str):
        """Broadcast that a result has been chosen in the search results."""

    def on_search_changed(self, search_widget: Gtk.SearchEntry):
        """Callback when search entry has changed."""
        search_text = search_widget.get_text().lower()

        self._filtered_country_list.update(search_text)
        if self._revealer:
            self._revealer.set_reveal_child(bool(search_text))

    def _on_row_activated(
        self,
        _listbox: FilteredList,
        row: FilteredListRow,
    ):
        selected_value = row.selected_value
        if not selected_value:
            return

        if self._revealer:
            self._revealer.set_reveal_child(False)
        self.emit("result-chosen", selected_value)
