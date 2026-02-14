
oled = False
import time

def loading_screen(oled):
    import LASK5_Loading_Screen
    for i in LASK5_Loading_Screen.frames:
        oled.buffer[:] = i
        oled.show()
        time.sleep(.1)


