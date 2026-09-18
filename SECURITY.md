# Security policy / Política de seguridad

## English

This project controls an Android TV through ADB and can install/uninstall apps, read and write files and reboot the device. Treat it as a privileged tool.

- Default mode listens on `127.0.0.1` only, rejects other `Host` headers and cross-site `Origin`s.
- `--lan` mode exposes it to your network behind a random per-run token over **plain HTTP**. Use it only on a trusted network; never port-forward it or expose it to the internet.
- The token travels in the URL (`?t=`), so it can end up in browser history. Do not share those URLs.

**Reporting a vulnerability:** please do not open a public issue. Use GitHub's *Security → Report a vulnerability* (private advisory) on this repository.

## Español

Este proyecto controla un Android TV por ADB y puede instalar/desinstalar apps, leer y escribir archivos y reiniciar el equipo. Trátalo como una herramienta privilegiada.

- El modo por defecto escucha solo en `127.0.0.1`, rechaza otros `Host` y `Origin` de otros sitios.
- El modo `--lan` lo expone a tu red con un token aleatorio por ejecución sobre **HTTP sin cifrar**. Úsalo solo en una red de confianza; nunca abras el puerto en el router ni lo publiques en internet.
- El token viaja en la URL (`?t=`), por lo que puede quedar en el historial del navegador. No compartas esas URLs.

**Reportar una vulnerabilidad:** no abras un issue público. Usa *Security → Report a vulnerability* (aviso privado) de GitHub en este repositorio.
