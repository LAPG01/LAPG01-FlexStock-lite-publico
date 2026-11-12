"""
Abrir un archivo con el programa que el sistema tenga para ese tipo
(el visor de PDF, en la practica). Corre en Windows y en Arch Linux
(regla 8 del CLAUDE.md): cada sistema tiene su propia forma.
"""

import os
import subprocess
import sys
from pathlib import Path


def reveal_in_folder(path: Path) -> None:
    """
    Abre la carpeta donde esta 'path'. En Windows y macOS ademas deja el
    archivo marcado; en Linux no hay una forma igual en todos los
    escritorios, asi que solo se abre la carpeta. Mismas reglas que
    open_in_viewer: Popen sin shell, y OSError si el sistema no tiene con
    que abrirla.
    """
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def open_in_viewer(path: Path) -> None:
    """
    Abre 'path' sin esperar a que el visor se cierre (Popen, no run: la
    ventana de la app tiene que seguir respondiendo). Sin shell=True: la
    ruta se pasa como argumento, nunca se interpreta como comando.

    Lanza OSError si el sistema no tiene con que abrirlo (ej. un Linux
    sin xdg-open); quien llama lo traduce a un aviso.
    """
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
