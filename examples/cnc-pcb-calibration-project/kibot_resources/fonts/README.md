# Fonts

The schematic and PCB sheets use **Arial** and **Times New Roman**. They are
not redistributable, so the container uses metric-compatible free fonts
(same character widths: the text keeps its size and position):

| Sheet font | Container font | License |
| --- | --- | --- |
| Arial | Liberation Sans 2.1.5 | SIL Open Font License 1.1 ([LICENSE-Liberation.txt](LICENSE-Liberation.txt)) |
| Times New Roman | Liberation Serif 2.1.5 | SIL Open Font License 1.1 |

KiBot installs the fonts of this folder in the container before each run, and
fontconfig substitutes them for Arial / Times New Roman. KiCad on Windows and
macOS uses the real fonts. On Linux, install `fonts-liberation`.

Source: https://github.com/liberationfonts/liberation-fonts (release 2.1.5).
