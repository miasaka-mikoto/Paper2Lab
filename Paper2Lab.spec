# Generated/maintained PyInstaller spec. Use build_windows.ps1 on Windows.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('paper2lab')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    hiddenimports=hiddenimports,
    datas=[('sample', 'sample')],
    binaries=[],
    # Optional scientific/PDF stacks are intentionally not pulled into the
    # standalone desktop build. The source parser uses them only when they
    # are already installed; the frozen app retains its dependency-free PDF
    # text fallback instead of ballooning into a hundreds-of-megabytes bundle.
    excludes=['fitz', 'pymupdf', 'numpy', 'pandas', 'matplotlib', 'scipy', 'PySide6'],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='Paper2Lab',
          debug=False, bootloader_ignore_signals=False, strip=False,
          upx=True, console=False)
