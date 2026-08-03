# plugin-inventar

Liest ein Claude-Code-Plugin-Verzeichnis und meldet, was darin liegt: welche Hooks an
welchen Events **welches Kommando** ausführen, welche MCP-Server wohin gehen, was in den
PATH kommt, was an Einstellungen überschrieben wird.

Beim nächsten Lauf meldet es, **was sich seither geändert hat**. Das ist der eigentliche
Zweck: Plugins aktualisieren sich im Hintergrund selbst, und die Version, die du geprüft
hast, ist nicht ewig die, die bei dir läuft.

**Es inventarisiert und vergleicht. Es bewertet nicht.**

## Wozu, wenn es `claude plugin details` gibt

Die eingebaute Ansicht beantwortet die meisten Fragen, und für den schnellen Blick vor
einer Installation reicht sie. Drei Dinge kann sie nicht:

| | `claude plugin details` | `plugin-inventar` |
|---|---|---|
| Hooks | Event-Name | Event, Matcher **und Kommando im Wortlaut** |
| Befehle und Skills | beides zusammen als „Skills" | getrennt, mit „nur auf Aufruf" |
| MCP-Server | Anzahl und Name | Transport, Ziel, erwartete Variablen |
| `bin/`, `settings.json` | nicht gezeigt | gemeldet |
| Nicht installiertes Verzeichnis | nur über `--plugin-dir`, **das lädt das Plugin** | liest nur, lädt nichts |
| Vergleich mit früher | nein | ja, das ist die Kernleistung |

Die letzte Zeile ist der Daseinsgrund. Die vorletzte ist der Grund, warum es ein eigenes
Werkzeug ist und kein Aufruf des vorhandenen: Ein Werkzeug, das *vor* der Installation
schauen soll, darf das Plugin nicht starten.

    plugin-inventar                     alle installierten Plugins aufnehmen
                                        und vergleichen
    plugin-inventar PFAD                nur dieses Verzeichnis
    plugin-inventar PFAD --json         Zustand als JSON
    plugin-inventar PFAD --as KEY   compare under this key
    plugin-inventar --no-save   Lauf ohne zu quittieren

## Installieren

```
claude plugin marketplace add <benutzer>/plugin-inventar
claude plugin install plugin-inventar@appx-patterns
```

Der Katalog liegt im Repo selbst (`.claude-plugin/marketplace.json`) und zeigt mit `./`
auf die Wurzel. Ein absoluter Pfad als Quelle wird abgelehnt, ein relativer und eine
GitHub-Angabe funktionieren.

Danach steht der Befehl `/plugin-inventar:stand [PFAD]` bereit, und `plugin-inventar`
liegt als blanker Befehl im PATH des Bash-Werkzeugs, solange das Plugin aktiv ist.

## Benutzung

**Der erste Lauf ist der wichtige.** Ohne Argument geht das Werkzeug über alle
installierten Plugins und nimmt den Vergleichsstand auf. Vorher hat es nichts, wogegen
es vergleichen könnte, und der Nutzen entsteht erst beim zweiten Lauf. Einmal nach dem
Installieren laufen lassen, dann in zwei Wochen wieder:

```
$ plugin-inventar
10 Plugins aufgenommen. Das ist ab jetzt dein Vergleichsstand.

Neu aufgenommen
  ein-plugin@ein-markt
  noch-eins@ein-markt
  abgeschaltet@ein-markt  [deaktiviert]
  …

3 davon sind deaktiviert. Sie laufen nicht, aktualisieren sich aber weiter.
```

Deaktivierte Plugins laufen mit. Sie tun nichts, aber sie aktualisieren sich weiter,
und wer eins wieder einschaltet, schaltet die neuere Version ein, nicht die geprüfte.

Die Liste der installierten Plugins kommt aus Claude Codes eigener Registry, nicht aus
einem Durchlauf durch den Cache. Dort liegen alte Versionen neben den laufenden — auf der
Maschine, auf der das gebaut wurde, ein Plugin in zwei und ein anderes in vier Fassungen —
und nur die Registry weiß, welche davon läuft.

Pfade mit Leerzeichen beim Slash-Befehl in Anführungszeichen setzen.

Rückgabewerte: `0` gelesen (auch mit Befunden und auch bei Änderungen, ein Befund ist
kein Fehler), `1` Pfad nicht lesbar oder Aufruffehler, `2` dort liegt kein Plugin,
`120` Ausgabe-Pipe geschlossen, `130` mit Strg-C beendet.

Mit `--as` lässt sich ein frisch geklontes Verzeichnis gegen den installierten Stand
halten. Das beantwortet die Frage „was ändert sich, wenn ich dieses Update einspiele",
**bevor** man es einspielt.

### Beispiel

Dasselbe Plugin in zwei Versionen:

```
$ plugin-inventar ~/.claude/plugins/cache/beispiel/werkzeug/0.1.3 --as werkzeug@markt
...
Kein Vergleichsstand vorhanden, dies ist der erste Lauf.

$ plugin-inventar ~/.claude/plugins/cache/beispiel/werkzeug/0.2.0 --as werkzeug@markt
...
Verglichen mit dem Stand vom 2026-07-28T09:52:01Z (Schlüssel: werkzeug@markt)
Änderungen seit dem letzten Lauf
~ Version  vorher: 0.1.3  jetzt: 0.2.0
~ Skill werkzeug
    Fundstelle  vorher: SKILL.md
                jetzt:  skills/werkzeug/SKILL.md
    im Plugin-Root  vorher: ja
                    jetzt:  nein
- Befehl werkzeug
```

Aus einem Befehl ist ein Skill geworden. Der Unterschied ist nicht kosmetisch: Einen Befehl
rufst du auf, einen Skill zieht das Modell selbst heran, wenn es ihn für passend hält.

## Was es nicht tut

Es liest Konfiguration, **keinen Code**. Welches Kommando ein Hook aufruft, steht im
Bericht. Was das aufgerufene Skript tut, liest es nicht. Der Bericht sagt das auch selbst.

Es gibt keine Ampel und keine Bewertung, weil das Werkzeug sie nicht belegen könnte.
Markiert wird nur, was wörtlich in der Datei steht: ob ein Kommando etwas nachlädt
(`curl`, `npx`, …) und ob es aus dem Plugin-Verzeichnis herauszeigt.

Eine Einschränkung, die ehrlicherweise dazugehört: Die **Reihenfolge** der Ausgabe ist
bereits ein Urteil. Es ist dieses: Was ohne dein Zutun läuft, kommt zuerst.

## Der ehrliche Punkt

`bin/` legt eine ausführbare Datei in den PATH des Bash-Tools, solange das Plugin aktiv
ist. Das ist einer der Punkte, die dieses Werkzeug bei anderen meldet. Es taucht also in
seinem eigenen Bericht auf:

```
plugin-inventar 0.1.0 · 1 bin/ · 1 Command

bin/
  plugin-inventar

Commands
  stand (on request only)
      "${CLAUDE_PLUGIN_ROOT}/bin/plugin-inventar" $ARGUMENTS 2>&1
      may use: Bash

Checked and not present: Agents, Hooks, LSP servers, MCP servers, Monitors, Output
                         styles, Settings, Skills, Themes, Workflows

No baseline yet, this is the first run.
```

Von zwölf möglichen Komponenten-Arten sind zwei belegt. Kein Hook, weil ein Hook ungefragt
liefe, also genau die Eigenschaft, die dieses Werkzeug bei anderen sichtbar macht. Und ein
Befehl statt eines Skills, weil ein Skill vom Modell selbst gezogen werden kann.

**Und die unangenehmste Zeile ist `may use: Bash`.** Der Befehl führt das Skript über einen
`!`-Aufruf aus, und das braucht eine Bash-Berechtigung. Engere Muster wie
`Bash(plugin-inventar:*)` greifen nicht, weil das Kommando mit dem vollen, zur Laufzeit
eingesetzten Pfad beginnt. Es steht also die weiteste Variante da.

Das ist keine Ausrede, sondern der Punkt: Es gibt keine saubere und keine schmutzige Seite.
Jedes Plugin legt etwas in dein System. Die Frage ist nur, ob du weißt, was.

## Anforderungen

Python 3.9 oder neuer, nur Standardbibliothek. Keine Abhängigkeiten.

## Tests

    python3 -m unittest discover -s tests

Die Datei `tests/test_criteria.py` enthält die Zusagen aus `docs/design.md` als
ausführbare Tests. Schlägt dort etwas fehl, hält das Werkzeug eine Zusage nicht ein,
die es öffentlich macht.

## Doku

- [docs/design.md](docs/design.md) — Entwurf, Entscheidungen und was bewusst verworfen wurde

## Lizenz

MIT
