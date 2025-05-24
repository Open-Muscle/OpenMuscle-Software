'''
# OpenMuscle Embedded Firmware

This directory contains embedded firmware for all OpenMuscle devices, organized first by hardware type (e.g., LASK5, FlexGrid), and then by version (e.g., LASK5-V2, LASK5-V3).

Each folder may include firmware written in different programming languages, such as MicroPython or C, depending on the specific implementation.

## 🗂 Directory Layout

Example folder structure:

- 'LASK5/LASK5-V2/' – MicroPython firmware for LASK5 Version 2  
- 'LASK5/LASK5-V3/' – C-based firmware under active development  
- 'FlexGrid/FlexGrid-V0/' – MicroPython firmware for the 60-sensor FlexGrid  
- 'SensorBand/OM12-V1/' – Legacy OM12 pressure band firmware

Each subfolder contains relevant 'main.py', 'config', or '.c/.h' files along with device-specific instructions.

## 📜 Licensing & Contributions

All embedded code is open source, licensed under MIT or CERN-OHL-S-2.0.  
A detailed contributor guide will be added soon to explain how to submit firmware and be recognized in the repository.

## 📬 Contact

Questions or want to contribute? Visit [openmuscle.org](https://openmuscle.org) or file an issue on GitHub.

'''
