# iBackup

Lokale Browser-App. Öffnet iOS-Backups in einer iPod-Ansicht mit Fotos,
Videos, Nachrichten, Kontakten, Notizen und Sprachmemos. Nur lesend.

## Start

Auf dem Mac `start.command` doppelklicken. Alternativ:

```sh
./start.command
```

Python 3.12 und die wenigen Abhängigkeiten werden über
[uv](https://docs.astral.sh/uv/getting-started/installation/) bereitgestellt.
Beim ersten Start ist dafür eine Internetverbindung nötig. Danach arbeitet
die App lokal und lädt keine externen Inhalte.

Die App öffnet `127.0.0.1:8765` im Browser. Der Startlink enthält einen
zufälligen Sitzungsschlüssel; danach bleibt die Sitzung in einem HttpOnly-Cookie.
Das lokale Backup wird automatisch gefunden. Andere Backupordner lassen sich
über die Oberfläche auswählen. Das Backup-Passwort nur in der App eingeben.
Es wird weder gespeichert noch geloggt.

```sh
./start.command --backup "/Pfad/zum/Backup" --port 8766
```

Beenden: `Ctrl+C` im Serverfenster. **Backup sperren** verwirft die geladenen
Datenbanken und Schlüssel aller geöffneten Backup-Stände. Es wird keine entschlüsselte Kopie auf Platte
geschrieben. Python kann keine garantierte physische Speicherlöschung zusagen.

## Umfang

- MBDB-Backups, Schwerpunkt iOS 9. Verschlüsselt oder unverschlüsselt.
- Fotos/Videos aus `CameraRollDomain/Media/DCIM`; Browser-Codec entscheidet
  über die Darstellung. Videos werden stückweise gelesen.
- Klassische iOS-Adressbücher, SMS/iMessage-Chats, `notes.sqlite` und
  gzip/Protobuf-Text aus `NoteStore.sqlite`. Geschützte Einzelnotizen bleiben
  geschützt; unlesbarer Text, Vorschauen und Anhänge werden gekennzeichnet.
- Sprachmemos: lokaler Audio-Player für vorhandene Apple-Aufnahmen;
  Titel/Dauer/Datum aus unterstützten Recordings-Datenbanken.
- Suche in Kontakten, Notizen und Gesprächsübersichten; ältere Nachrichten
  lassen sich nachladen. Nachrichtenanhänge erscheinen aktuell als Hinweis.
- Keine Bearbeitung, Wiederherstellung oder Verbindung zu iCloud.

Neuere `Manifest.db`-Backups, Datenbanken mit
nichtleerem WAL und nicht unterstützte Schemas bekommen eine klare Meldung.
In iCloud ausgelagerte und nicht im Backup enthaltene Medien fehlen auch hier.
Die erste Version konzentriert sich auf ältere MBDB-Backups. Sie
verspricht keine vollständige Unterstützung aller iOS-Versionen.

## Zeitmaschine

Unter **Zeitmaschine** zwischen datierten Backups desselben Geräts wechseln.
Weitere Ordner über **Backup-Stand hinzufügen** hinzufügen und einmal
pro Sitzung entsperren. Die zusätzlichen Pfade und Schlüssel werden nur für
diese laufende Sitzung behalten. Ein einzelnes Backup ist genau ein Stand;
Datei-Zeitstempel erzeugen keine früheren vollständigen Geräteansichten.

Jeden Backup-Stand separat aufbewahren, etwa unter `Backup/Stand-2025/`
und `Backup/Stand-2026/`. Jeder Ordner braucht seine eigene `Info.plist`,
`Manifest.plist`, `Manifest.mbdb` und die zugehörigen Dateien. Die Ordnernamen
dürfen sich unterscheiden; die Gerätezuordnung stammt aus den Metadaten.
Mit **Backups erneut suchen** werden neu kopierte Ordner im Suchverzeichnis
gefunden. Andere Pfade lassen sich mit **Backup-Stand hinzufügen** öffnen.

Beim Zusammenführen gleichnamiger Ordner geht die Zuordnung der einzelnen
Stände verloren. Zusätzliche Dateien ohne Index und fehlende Dateien werden
angezeigt. Unzugeordnete Dateien werden weder als gelöscht noch als zweiter
Zeitstand ausgegeben. Für eine vollständige Zeitreise die zweite Kopie vom
Quellcomputer nochmals in einen eigenen Ordner übertragen.

**Vergangene & gelöschte Inhalte** zeigt zusätzlich:

- **Gelöscht:** Eintrag trägt eine erhaltene Löschmarkierung, etwa in
  Foto-Metadaten, Notizen oder einer unterstützten Datenbank.
- **Historisch:** Eintrag wurde nur in einem älteren entsperrten Backup
  gefunden. Das belegt keine Löschung. Quelle und Datum bleiben sichtbar.
- **Fragment:** Erhaltener Notiztext ohne zugehörigen vollständigen Notizeintrag.

Ältere Versionen geänderter Inhalte erreichst du durch Auswahl des damaligen
Stands. In der gemeinsamen Ansicht gewinnt der gewählte Stand bei gleicher
Eintragskennung. Nicht entschlüsselte, andere Geräte und spätere Stände werden
nicht zugemischt. Gelöschte SQLite-Seiten werden nicht forensisch rekonstruiert;
überschriebene oder nicht gesicherte Inhalte lassen sich hier nicht zurückholen.

## Prüfung

```sh
uv run pytest -q
node --test tests/test_ui.mjs
```

Tests verwenden synthetische verschlüsselte Backups und echte SQLite-Dateien
mit erfundenen Inhalten. Geprüft werden Entschlüsselung, Passwortfehler,
Dateibereiche, unveränderte Quellen, alle fünf Datenleser, Snapshot-Herkunft und lokaler
Zugriffsschutz. Echte verschlüsselte Inhalte müssen nach lokaler Eingabe des
Backup-Passworts separat überprüft werden.

Formatreferenzen: [MBDB](https://github.com/dinosec/iphone-dataprotection/blob/master/python_scripts/backups/backup4.py),
[Keybag](https://github.com/dinosec/iphone-dataprotection/blob/master/python_scripts/keystore/keybag.py),
[RFC-3394 in cryptography](https://cryptography.io/en/latest/hazmat/primitives/keywrap/).
NoteStore-Feldschema: [apple_cloud_notes_parser](https://github.com/threeplanetssoftware/apple_cloud_notes_parser/blob/master/proto/notestore.proto).
