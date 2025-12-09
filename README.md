# MIDI → sidecar YAML (GUI)

Plantilla con **PySide6 + qdarkstyle**, barra de progreso, panel de logs y build automático para Windows (.exe) usando **GitHub Actions** y **PyInstaller** en modo **onedir**.

## Estructura
- `app.py` — GUI con selector de `.mid`, progreso, logs; guarda `*.yml` junto al `.mid`.
- `midi_to_sidecar.py` — motor de conversión (reutiliza tu script).
- `requirements.txt` — dependencias (no hace falta instalarlas localmente si usas Actions).
- `pyinstaller.spec` — (opcional) configuración de PyInstaller. Puedes borrarlo si prefieres usar la CLI por defecto.
- `.github/workflows/build.yml` — workflow de GitHub para compilar en Windows y subir artefactos.
- `icons/app.ico` — (opcional) icono; si existe, se usa automáticamente.

## Uso local (opcional)
```bash
pip install -r requirements.txt
python app.py
```

## Build en la nube (Windows)
1. Crea un repo en GitHub y sube estos archivos.
2. Haz push a `main`.
3. Ve a **Actions** → workflow **Build Windows EXE (PyInstaller onedir)**.
4. Al terminar, descarga el artefacto **midi2yaml_gui-windows** (carpeta `dist/` con el ejecutable).

## Notas
- El GUI ignora el canal 10 (drums), detecta tonalidad (mayor / menor natural / menor armónica) y escribe romanos por compás (mitades si cambia: `I|V`).
- Los logs se ven en la ventana y también se guardan en `~/.midi2yaml_gui.log`.
- Si quieres icono, coloca `icons/app.ico`. El `.spec` lo usará automáticamente.
