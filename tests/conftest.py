"""
Defensas comunes a toda la suite.

Lo que vive aca existe para que el resultado de `pytest` dependa SOLO
del codigo y no del humor de la maquina (ni moleste a quien la usa):

1. `esperar`: en vez de dormir una cantidad fija de milisegundos y
   cruzar los dedos, corre el mainloop de Tk hasta que se cumpla una
   condicion. Un `time.sleep(0.2)` seguido de un `update()` suelto
   es una carrera: si la maquina esta cargada (o corriendo con
   coverage, que agrega ~30% de overhead) esos 200 ms se pasan sin que
   el `after()` haya alcanzado a correr, y el test falla sin que nada
   este roto. Ademas es mas rapido: vuelve apenas pasa lo que se
   esperaba, no cuando se agota el plazo.

2. `_prohibir_red`: ningun test puede salir a internet sin querer.
   ExchangeRateService lee tasas de `settings` y nunca toca la red por
   su cuenta, pero MainWindow si dispara `fetch_rate_now()` y companeros
   en un hilo al arrancar. Hasta ahora eso lo frenaba una fixture que
   solo existe en test_main_window.py (sembrar la cache del dia); si
   otro archivo construye una ventana, sale a internet de verdad: lento,
   dependiente de que haya conexion, y con un resultado que cambia segun
   lo que responda el BCV ese dia. Aca se corta de raiz, para toda la
   suite. Un test que SI quiera probar la red parchea `urlopen` por su
   cuenta (ver test_exchange_rate_service.py): su monkeypatch pisa a
   este, que es autouse y por lo tanto corre antes.
"""

import os
import shutil
import subprocess
import time
import tkinter as tk
import urllib.request

import pytest

# ------------------------------------------------------------
# 3. Una pantalla invisible para las ventanas de los tests
# ------------------------------------------------------------
# La suite abre cientos de ventanas Tk (y los tests de diseno, a
# proposito, las MUESTRAN: ver mostrar_con_tamano). Sobre la pantalla de
# verdad eso aparece, roba el foco y no deja escribir mientras corre.
# Si Xvfb esta instalado (Arch: xorg-server-xvfb), se arranca uno para
# toda la sesion y las ventanas van ahi: existen, se miden y se dibujan
# igual, pero nadie las ve. Sin Xvfb (Windows, o si no se instalo) se
# usa la pantalla normal, como siempre.
#
# FLEXSTOCK_TESTS_EN_PANTALLA=1 fuerza la pantalla real (para MIRAR
# un test de diseno mientras corre).
_xvfb: subprocess.Popen | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _xvfb
    if os.environ.get("FLEXSTOCK_TESTS_EN_PANTALLA") == "1":
        return
    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        return
    # -displayfd: Xvfb elige un numero de pantalla libre y lo escribe por
    # ese descriptor cuando ya esta listo para conexiones. Sin adivinar
    # ":99" (que podria estar ocupado) y sin esperar con sleep.
    lectura, escritura = os.pipe()
    _xvfb = subprocess.Popen(
        [
            xvfb,
            "-displayfd",
            str(escritura),
            "-screen",
            "0",
            "1920x1080x24",
            "-nolisten",
            "tcp",
        ],
        pass_fds=(escritura,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(escritura)
    with os.fdopen(lectura) as canal:
        numero = canal.readline().strip()
    if not numero:  # no pudo arrancar: pantalla normal
        _xvfb.kill()
        _xvfb = None
        return
    os.environ["DISPLAY"] = f":{numero}"
    os.environ.pop("WAYLAND_DISPLAY", None)


def pytest_unconfigure(config: pytest.Config) -> None:
    if _xvfb is not None:
        _xvfb.terminate()
        _xvfb.wait(timeout=5)


@pytest.fixture(autouse=True)
def _prohibir_red(monkeypatch):
    def _bloqueado(*_a, **_k):
        raise AssertionError(
            "Un test intento salir a la red de verdad. Si es a proposito, "
            "parchea urlopen en el test; si no, sembra la cache de tasas "
            "(ver la fixture _sin_llamadas_de_red_por_defecto)."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _bloqueado)


@pytest.fixture(autouse=True)
def _ctk_en_modo_claro():
    """
    El modo claro/oscuro de CustomTkinter es GLOBAL al proceso (no de
    cada ventana): un test que prueba el boton de tema dejaria a todos
    los siguientes en oscuro. Se vuelve a claro al terminar cada uno.

    De paso se olvidan las ventanas ya destruidas que CTk sigue
    recordando: su bucle de modo prueba cada 30 ms con TODAS, y a lo
    largo de mil tests la lista solo crece.
    """
    yield
    import customtkinter as ctk

    ctk.set_appearance_mode("light")
    tracker = ctk.AppearanceModeTracker
    vivas = []
    for app in tracker.app_list:
        try:
            if app.winfo_exists():
                vivas.append(app)
        except tk.TclError:
            pass
    tracker.app_list[:] = vivas


@pytest.fixture
def esperar():
    """
    esperar(widget, lambda: <ya paso?>, que="descripcion")

    Corre `widget.update()` hasta que la condicion da True. Si a los
    `limite` segundos sigue sin cumplirse, falla con un mensaje que dice
    que se estaba esperando (mucho mas util que un assert pelado sobre
    una lista vacia). El limite es generoso a proposito: no es el tiempo
    que tarda el test, es el techo antes de declarar que algo se colgo.
    """

    def _esperar(widget, condicion, *, limite: float = 5.0, que: str = "") -> None:
        fin = time.monotonic() + limite
        while True:
            widget.update()
            if condicion():
                return
            if time.monotonic() >= fin:
                raise AssertionError(
                    f"Se agotaron {limite}s esperando"
                    + (f" a que {que}." if que else ".")
                )
            time.sleep(0.005)

    return _esperar


@pytest.fixture
def mostrar_con_tamano(esperar):
    """
    mostrar_con_tamano(raiz, ventana, "1300x700")

    Para los tests que MIDEN el diseno (que un boton no quede cortado,
    que un panel entre en la ventana). Muestra la ventana de verdad y
    espera a que el gestor de ventanas le aplique el tamano.

    Por que no alcanza con la ventana oculta, como en el resto de la
    suite: Tk no termina de acomodar los widgets de una ventana que
    nunca se mostro. Segun en que orden lleguen los eventos, un boton
    empacado adentro de un marco ubicado con grid se queda en 1 px de
    alto aunque su marco ya tenga lugar de sobra (se reproduce hasta con
    dos tk.Frame pelados). Con ttk esos tests pasaban de casualidad;
    con los widgets de CustomTkinter el orden cambio y fallaban sin que
    la ventana real tuviera nada mal.
    """

    def _mostrar(raiz, ventana, geometria: str) -> None:
        ancho, alto = (int(v) for v in geometria.split("x"))
        raiz.deiconify()
        ventana.deiconify()
        tk.Wm.wm_geometry(ventana, geometria)
        esperar(
            ventana,
            lambda: (
                ventana.winfo_ismapped()
                and ventana.winfo_width() == ancho
                and ventana.winfo_height() == alto
            ),
            que=f"se muestre la ventana con {geometria}",
        )

    return _mostrar
