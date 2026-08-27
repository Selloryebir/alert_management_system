# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


repository_root = Path(SPECPATH).resolve().parents[1]
algorithm_root = repository_root / "src" / "algorithm"
training_root = repository_root / "tools" / "model-training"
skops_protocol_modules = [
    "skops.io.old._general_v0",
    "skops.io.old._numpy_v0",
    "skops.io.old._numpy_v1",
]

analysis = Analysis(
    [str(training_root / "train.py")],
    pathex=[str(algorithm_root), str(training_root)],
    binaries=[],
    datas=[
        (
            str(training_root / "data" / "engineering-scenarios.jsonl"),
            "model-data",
        )
    ],
    hiddenimports=(
        collect_submodules("sklearn")
        + collect_submodules("skops")
        + skops_protocol_modules
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "httpx", "httpcore", "fastapi", "uvicorn"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="model-provisioner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="model-provisioner",
)
