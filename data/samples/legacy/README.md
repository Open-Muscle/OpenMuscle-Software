# Legacy sample recordings

These 20 files (`flexgrid_merged_*.csv`) are early recordings from the first
FlexGrid bands, kept as small samples of the old v1 file format. They used to
sit in the folder `openmuscle web` saved new takes into, which is why they are
here now: your own recordings go to `~/OpenMuscle/captures` and start empty.

Format: one header row, then `timestamp`, 64 pressure cells `R0C0` to `R3C15`
(4 rows by 16 columns) and four finger labels `label_0` to `label_3` from the
LASK5 finger labeler. Some files hold only a header or a few rows. They carry no
sidecar files and predate the current format, so treat them as reference
samples of the older layout rather than training data.
