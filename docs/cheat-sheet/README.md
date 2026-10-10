# Starter kit cheat sheet

A four-page, print-ready quick reference for anyone who receives an OpenMuscle starter kit. It follows the first day in order (charge, switch on, install, start, Wi-Fi, heatmap, camera, record, train, predict, send back). The band's buttons are explained on the board picture on page 1; its lights, screen and fit are on page 2 under the label "Reference". It fits US Letter and A4.

It is written for a clinician or participant who is not an engineer. It never names a person, a site, a study, a city or a participant.

## Files

| File | What it is |
|---|---|
| `openmuscle-kit-cheat-sheet.html` | The sheet. Hand-edited, self-contained apart from the pictures in `images/` (relative paths). Open it in any browser. |
| `openmuscle-kit-cheat-sheet.pdf` | The printed result, built by the script below. |
| `build_pdf.py` | Prints the HTML to the PDF with Edge, Chrome or Chromium. Standard library only. |
| `images/` | The three pictures the sheet uses (see below). |

## Rebuild the PDF

```
python build_pdf.py             # paper size from the browser default (Letter on most US machines)
python build_pdf.py --paper a4  # force A4
python build_pdf.py --paper letter
```

The script finds Microsoft Edge or Google Chrome (or Chromium on Linux and macOS). To use a specific browser, set `OPENMUSCLE_BROWSER` to its full path. It starts the browser with a throwaway profile, so a browser you already have open is left alone. It exits with a non-zero status if no browser is found, no PDF appears, or the page count is outside 2 to 4. Any change to the HTML needs a rebuild, and the whole PDF should be read back page by page after it.

## Layout rules to keep when editing

- Each of the four `.page` blocks is a fixed box (260 mm tall inside an 8 mm page margin). If a page overflows, its footer is pushed off the sheet and a fifth page appears, so the script fails. Keep at least 5 mm of free space on every page, measured at A4 text width (the tighter case); a different font on another machine can change line breaks. The sheet was laid out and checked with Segoe UI (Windows). Without it the browser falls back to Helvetica Neue or Arial, which are wider, so on a Mac or Linux machine check the page count and the free space again.
- Text sizes: body text and step lists are 10 pt; tables, notes, legends, cards and notices are 9 to 9.5 pt; the page footer is 9 pt; the small section-label chips are 8 pt; the amber tag is 7 pt; the letters inside picture badges are 7 to 8.5 pt. Nothing else is smaller. Keep it that way: if a change does not fit, cut words, not point size. The four-page limit is why the sheet is short on detail; the full guides are in this folder's parent, `docs/`.
- The footers say "Page N of 4" by hand. If the page count ever changes, update all four.
- No em dashes or en dashes anywhere in the text. Use commas, colons, semicolons or two sentences. Straight quotes only (curly quotes come out as broken characters when text is copied from the PDF). US spelling (color, centered, centimeter) to match the US Letter default.
- Color is never the only clue: every light is drawn as a pattern strip and described in words.
- The amber **to confirm** tag marks anything not yet checked on a physical band or computer, or wording not yet approved. When a fact is settled, remove its tag in the HTML. Items carrying the tag today: which switch is the power button and whether it is pressed or held, SW_RST1, the CHRG light color, charge and running times, the sheet color in the render, light and screen visibility through a case, the SD card option, the Mac installer steps, and the whole "Wearing it" block (the hardware owner has not yet confirmed the placement wording).
- The dark badges on the board picture mean "taken from the board design files" (the KiCad project), not "seen on a real band". Amber badges are facts nobody has yet seen on a real band.
- Facts come from the firmware's `docs/device-reference.md`, and from `getting-started.md`, `wifi-setup.md`, `band-fit.md`, `first-model.md`, `movement-sheet.md`, `your-data.md`, `troubleshooting.md` and `installers/README.md` in this repository. Where the device reference says a fact still needs a physical band, the sheet says so rather than guess.

## About the images

The pictures in `images/` are **downscaled** copies, not originals, so the repository stays small and the PDF stays a few megabytes.

- `board-top.png` and `device-angle.png` are 3D renders of the V4 board (top view, and on a forearm-shaped sensor cuff), taken from a 240-frame turntable and reduced to roughly 1 MB each. The real color of the sensor sheet is not confirmed; the render draws it yellow. The render shows no strap.
- `studio-live.png` is a screenshot of the Studio in its practice mode (made-up bands and data), 1100 pixels wide. It was taken before the **Help** link was added to the top bar, so the sheet says so under the picture. Retake it to remove that remark.
- The lettered callouts on the pictures, and the leader lines on the board, are drawn in the HTML (percent positions over the picture and an SVG overlay), so the picture files stay untouched. Crops are done in CSS, not in new files.

Replace a picture by keeping its file name and pixel size, or update the percent positions of its callouts in the HTML.
