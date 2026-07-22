# Avatar output backends

Project Akira's embedded VRM renderer is the default avatar output. The older
VMC/OSC controller remains available for VSeeFace and compatible receivers.

## Modes

- `embedded` — animate the VRM in Project Akira only.
- `both` — animate the embedded VRM and send VMC output simultaneously.
- `vmc` — send VMC output only; the avatar window remains a static preview.
- `disabled` — disable embedded animation and VMC transmission.

The global `avatar.enabled` setting can disable all avatar animation without
changing the selected backend.

VMC settings are loaded when the Python avatar controller is imported. Restart
Project Akira after changing the backend, receiver address, or receiver port.
The embedded renderer responds to settings changes immediately.

## VSeeFace

Enable the OSC/VMC receiver in VSeeFace and leave its receiver port at `39539`
unless the matching Project Akira setting is changed. Use `both` when you want
the built-in avatar and VSeeFace to react at the same time.
