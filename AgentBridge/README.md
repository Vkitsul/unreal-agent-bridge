# Agent Bridge

Plugin per **Unreal Engine 5.8** che permette a un agente AI (Claude Code o un altro client MCP) di leggere, modificare e compilare i Blueprint come testo.

È scritto solo in Python e usa quello già incluso in Unreal: niente da compilare, niente Visual Studio, niente da installare.

> **Stato:** testato solo contro una simulazione dell'API documentata da Epic, non ancora dentro Unreal. L'autotest qui sotto serve a verificarlo sul motore vero.

## Installazione

1. Fai un commit del progetto o una copia di sicurezza.
2. Copia la cartella `AgentBridge` in `TuoProgetto/Plugins/`.
3. Apri il progetto. Nell'Output Log deve comparire:
   `AgentBridge: MCP server listening on http://127.0.0.1:19840/mcp`

## Autotest

Nella console dell'Output Log scrivi:

```text
py import agent_bridge.selftest as t; t.run()
```

Crea un Blueprint temporaneo, prova ogni funzione e lo cancella. Il report finisce nell'Output Log e in `Saved/AgentBridge_selftest.txt`.

## Collegare Claude Code

Con l'editor aperto:

```bash
claude mcp add --transport http unreal http://127.0.0.1:19840/mcp
```

Per cambiare porta, imposta la variabile d'ambiente `AGENT_BRIDGE_PORT` prima di avviare l'editor.

## Strumenti

| Strumento | A cosa serve |
|---|---|
| `blueprint_read` | Legge un Blueprint come testo compatto |
| `blueprint_edit` | Sostituisce un pezzo di testo, come in un file sorgente |
| `blueprint_write` | Riscrive interi grafi, aggiunge nodi, variabili e funzioni |
| `blueprint_compile` | Compila e restituisce errori e warning per nodo |
| `blueprint_search` | Cerca in tutti i Blueprint del progetto, anche quelli non caricati |
| `blueprint_list_node_types` | Elenca i nodi creabili per nome |
| `run_python` | Esegue Python nell'editor: asset, attori, livelli, Play In Editor, screenshot |
| `console_command` | Esegue un comando della console |

Ogni scrittura è tutto-o-niente: se qualcosa fallisce o il Blueprint non compila, le modifiche vengono annullate e l'agente riceve gli errori.

## Il formato

```text
blueprint /Game/BP_Hero.BP_Hero parent=Character status=UpToDate
var Health: {"type": "number"}
=== graph EventGraph
Event_0: Event BeginPlay @0,0
  then -> CallFunction_3.execute
CallFunction_3: Print String @320,0
  InString = Ciao
+attesa call KismetSystemLibrary.Delay @640,0
  Duration = 2.0
```

- I nodi esistenti sono `id: titolo @x,y`. I nodi nuovi sono `+etichetta tipo argomenti`.
- Sotto ogni nodo, `Pin -> altroId.Pin` è un collegamento (scritto sul pin di uscita) e `Pin = valore` è un valore.
- I nodi esistenti omessi da una sezione vengono cancellati.
- Tipi di nodo nuovi: `call`, `get`, `set`, `getlocal`, `setlocal`, `macro`, `event`, `customevent`, `branch`, `result`, `comment`, `entry`, `node Categoria|Nome`.
- `=== graph Nome (new function)` crea una funzione. `param in Nome: float` aggiunge un parametro. `var Nome: float` prima dei grafi aggiunge una variabile.

## Limiti

- Funziona **solo da Unreal 5.8**, e l'API Python usata è marcata come sperimentale da Epic.
- I nodi esistenti si riconoscono da titolo e pin: l'API non dice esattamente quale funzione o variabile chiamano. La ricerca lavora quindi su titoli, valori, variabili e nomi dei grafi.
- I commenti a fumetto sui singoli nodi non sono accessibili, e i riquadri di commento non si possono spostare.
- Togliere una riga `Pin = valore` non riporta il pin al valore predefinito.
- La ricerca copre gli asset Blueprint, non i Level Blueprint.
- Widget, materiali e altri asset non hanno strumenti dedicati: si passa da `run_python`.

## Sicurezza

Il server ascolta solo su `127.0.0.1`, non ha autenticazione e rifiuta le richieste provenienti da pagine web. L'agente però può modificare, salvare e cancellare dati del progetto ed eseguire Python: usa sempre il controllo versione.

## Test

```bash
python tests/test_agent_bridge.py
```
