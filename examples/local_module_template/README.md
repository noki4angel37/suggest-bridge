# Local module template

Copy `hello_module.py` **outside** the suggest-bridge repository (for example `~/suggest-bridge-modules/hello_module.py`). Do not commit your module to [noki4angel37/suggest-bridge](https://github.com/noki4angel37/suggest-bridge).

## Enable

```env
SB_MODULES=/absolute/path/to/hello_module.py:HelloModule
```

Windows:

```env
SB_MODULES=C:\path\to\hello_module.py:HelloModule
```

Validate before restart:

```bash
python -m bot.core.module_loader
```

Or scaffold a new file:

```powershell
.\scripts\modules\scaffold-local-module.ps1 -OutDir C:\path\to\modules
```

```bash
bash scripts/modules/scaffold-local-module.sh ~/suggest-bridge-modules
```

Wiki: [[Добавить-модуль]] · [[FAQ-модулей]] · [[Add-module-en]]
