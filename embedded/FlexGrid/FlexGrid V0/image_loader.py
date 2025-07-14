
oled = False
import time

def loading_screen(oled):
    import Flex_Intro
    for i in Flex_Intro.frames:
        oled.buffer[:] = i
        oled.show()
        time.sleep(.1)


