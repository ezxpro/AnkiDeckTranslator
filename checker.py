import zipfile, sys
for f in sys.argv[1:]:
    z = zipfile.ZipFile(f)
    media = [n for n in z.namelist() if n.isdigit()]
    print(f, "-", len(media), "media files,",
          round(sum(i.file_size for i in z.infolist()) / 1e6, 1), "MB uncompressed")