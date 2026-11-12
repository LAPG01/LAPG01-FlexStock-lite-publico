"""
Pedir un dato suelto (una tasa, el IVA, el motivo de una anulacion) en un
dialogo con el mismo aspecto que el resto de la app.

Misma firma que `tkinter.simpledialog.askstring`, a proposito: quien la
llama solo cambia el import (`from . import simpledialog`) y los tests
siguen parcheando `<modulo>.simpledialog.askstring` igual que antes. La
de tkinter arma una ventana tk clasica (botones grises, sin modo
oscuro) que desentonaba al lado de todo lo demas.
"""

from __future__ import annotations

import tkinter as tk

from . import kit


class _AskStringDialog(kit.Dialog):
    def __init__(
        self, parent: tk.Misc, title: str, prompt: str, initialvalue: str
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result: str | None = None

        cuerpo = kit.Frame(self, padding=24)
        cuerpo.pack(fill="both", expand=True)
        kit.Label(cuerpo, text=prompt, style="Heading.TLabel").pack(
            anchor="w", pady=(0, 10)
        )
        self._var = tk.StringVar(value=initialvalue)
        self._entry = kit.Entry(cuerpo, textvariable=self._var, width=28)
        self._entry.pack(fill="x")

        botones = kit.Frame(cuerpo)
        botones.pack(fill="x", pady=(18, 0))
        kit.Button(
            botones, text="Aceptar", command=self._aceptar, style="Accent.TButton"
        ).pack(side="right")
        kit.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

        self.bind("<Return>", lambda _e: self._aceptar())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        tk.Wm.wm_geometry(self, f"+{max(x, 0)}+{max(y, 0)}")
        self._posicion_inicial = (max(x, 0), max(y, 0))
        self._entry.focus_set()
        self._entry.select_range(0, "end")
        self.after_idle(self._tomar_foco)

    def _tomar_foco(self) -> None:
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass  # cerrado antes de llegar a mostrarse

    def _aceptar(self) -> None:
        self.result = self._var.get()
        self.destroy()


def askstring(
    title: str,
    prompt: str,
    *,
    initialvalue: str | None = None,
    parent: tk.Misc | None = None,
) -> str | None:
    """Devuelve el texto escrito, o None si se cancelo o se cerro."""
    if parent is None:
        parent = tk._get_default_root("use askstring")
    dialogo = _AskStringDialog(parent, title, prompt, initialvalue or "")
    parent.wait_window(dialogo)
    return dialogo.result
