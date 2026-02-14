from PIL import Image
import os

# Config
input_folder = 'FlexGrid'
output_py_file = 'Flex_Intro.py'
width, height = 128, 32  # Adjust to your display
invert = True  # Flip black and white (set to False if unnecessary)

def convert_image(image_path):
    im = Image.open(image_path).convert('1')  # 1-bit mode
    im = im.resize((width, height))
    if invert:
        im = Image.eval(im, lambda px: 255 - px)

    pixels = list(im.getdata())
    data = []

    for y in range(height // 8):  # Each byte represents 8 vertical pixels
        for x in range(width):
            byte = 0
            for b in range(8):
                px_idx = x + (y * 8 + b) * width
                if px_idx < len(pixels) and pixels[px_idx] == 0:
                    byte |= (1 << b)
            data.append(byte)
    return data

def process_folder_to_py(input_folder, output_py_file):
    files = sorted(f for f in os.listdir(input_folder) if f.lower().endswith('.png'))

    with open(output_py_file, 'w') as out:
        out.write('# Auto-generated MicroPython animation frames for 128x32 OLED\n\n')
        frame_names = []

        for i, file in enumerate(files):
            frame_name = f'frame{i+1}'
            frame_names.append(frame_name)
            image_path = os.path.join(input_folder, file)
            byte_data = convert_image(image_path)

            out.write(f'{frame_name} = bytearray([\n')
            for j in range(0, len(byte_data), 16):
                line = ', '.join(f'0x{b:02x}' for b in byte_data[j:j+16])
                out.write(f'    {line},\n')
            out.write('])\n\n')

        # Add the frame list
        out.write(f'frames = [{", ".join(frame_names)}]\n')

    print(f'✅ Wrote {len(files)} frames to {output_py_file}.')

# Run it
process_folder_to_py(input_folder, output_py_file)
