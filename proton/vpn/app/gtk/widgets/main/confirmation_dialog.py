"""
Module for the disconnect dialog that prompts the user for confirmation
upon logout or exit.


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

from typing import Callable, Optional, Union

from gi.repository import Pango

from proton.vpn import logging
from proton.vpn.app.gtk import Gtk
from proton.vpn.app.gtk.utils.safe_signal_connect import safe_signal_connect

logger = logging.getLogger(__name__)

_CONFIRMATION_DIALOG_KEY = "proton_confirmation_dialog"


class ConfirmationDialog(Gtk.Dialog):
    """Confirmation dialog widget."""

    WIDTH = 150
    HEIGHT = 200

    def __init__(
        self, message: Union[Gtk.Widget, str], title: str, yes_text: Optional[str] = None, no_text: Optional[str] = None
    ):
        super().__init__()
        self.set_title(title)
        self.set_default_size(self.WIDTH, self.HEIGHT)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self._content.set_margin_top(20)
        self._content.set_margin_bottom(20)
        self._content.set_margin_start(20)
        self._content.set_margin_end(20)

        self._actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._actions.set_halign(Gtk.Align.END)

        self._yes_button = Gtk.Button(
            label="_Yes" if not yes_text else yes_text,
            use_underline=True,
        )
        self._no_button = Gtk.Button(label="_No" if not no_text else no_text, use_underline=True)

        self._no_button.add_css_class("primary")
        self._yes_button.add_css_class("danger")

        safe_signal_connect(self._yes_button, "clicked", self._on_yes_clicked)
        safe_signal_connect(self._no_button, "clicked", self._on_no_clicked)

        widget: Gtk.Widget
        if isinstance(message, str):
            widget = Gtk.Label(label=message)
            widget.set_width_chars(50)
            widget.set_max_width_chars(50)
            widget.set_wrap(True)
            widget.set_wrap_mode(Pango.WrapMode.WORD)
        else:
            widget = message

        self._content.append(widget)
        self._actions.append(self._no_button)
        self._actions.append(self._yes_button)
        self._content.append(self._actions)
        self.set_child(self._content)

    @property
    def yes_button(self) -> Gtk.Button:
        return self._yes_button

    @property
    def no_button(self) -> Gtk.Button:
        return self._no_button

    def _on_yes_clicked(self, _button: Gtk.Button) -> None:
        self.emit("response", Gtk.ResponseType.YES)

    def _on_no_clicked(self, _button: Gtk.Button) -> None:
        self.emit("response", Gtk.ResponseType.NO)


def _clear_confirmation_dialog_reference(dialog: ConfirmationDialog, parent: Gtk.Window) -> None:
    if parent is not None:
        setattr(parent, _CONFIRMATION_DIALOG_KEY, None)


def _deliver_confirmation_response(
    dialog: ConfirmationDialog,
    response: int,
    parent: Gtk.Window,
    callback_result: Callable[[ConfirmationDialog, int], None],
) -> None:
    _clear_confirmation_dialog_reference(dialog, parent)
    callback_result(dialog, response)


def show_confirmation_dialog(  # pylint: disable=too-many-arguments
    parent: Gtk.Window,
    title: str,
    question: str,
    clarification: str,
    yes_text: str,
    no_text: str,
    callback_result: Callable[[ConfirmationDialog, int], None],
) -> ConfirmationDialog:
    """
    Shows a confirmation dialog with a question and a clarification.
    """
    container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    container.set_spacing(10)

    question_label = Gtk.Label(label=question)
    question_label.set_halign(Gtk.Align.START)

    clarification_label = Gtk.Label(label=clarification)
    clarification_label.set_halign(Gtk.Align.START)
    clarification_label.add_css_class("dim-label")

    container.append(question_label)
    container.append(clarification_label)

    dialog = ConfirmationDialog(message=container, title=title, yes_text=yes_text, no_text=no_text)
    dialog.set_default_size(400, 200)
    dialog.set_modal(True)
    dialog.set_transient_for(parent)
    setattr(parent, _CONFIRMATION_DIALOG_KEY, dialog)
    dialog.connect("response", _deliver_confirmation_response, parent, callback_result)
    dialog.connect("destroy", _clear_confirmation_dialog_reference, parent)
    dialog.present()
    return dialog
