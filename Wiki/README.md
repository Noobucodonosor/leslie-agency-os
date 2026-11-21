# Wiki Leslie (C.I.B.O.)

Questa Wiki è il **Second Brain** del progetto. Serve a rispondere a "perché" e "come", non solo a "cosa".
Il codice risponde a "cosa", il DB a "quanto". Qui viviamo il resto.

## Come si legge

- Ogni file tratta **un solo dominio**. Se ne serve un altro, seguilo con `[[link]]`.
- Se apri la cartella `Wiki/` come vault Obsidian, la Vista Grafo mostra le relazioni.
- File monotematici → aggiornamenti chirurgici, non riscritture.

## Indice

### Architettura
- [[architettura]] — componenti, dataflow end-to-end, quali file fanno cosa
- [[schema_db]] — tabelle, colonne, indici, invarianti sui dati
- [[deploy_e_infra]] — Cloud Run, Docker, persistenza GCS, cost control

### Motore
- [[flusso_scout]] — ciclo di scouting autonomo, streaming SSE, decisione salvataggio
- [[ai_analyzer]] — DeepSeek, prompt, scoring 0-10, cache, retry
- [[scraper]] — estrazione contenuto + email da homepage e pagine contatto
- [[query_pools]] — categorie, pesi utente, learned intelligence, custom query, esaurimento

### Contesto
- [[concetti_dominio]] — cos'è un lead, un premium, una nicchia, un selling angle
- [[decisioni]] — perché SQLite, perché DeepSeek, perché Places, perché GCS
- [[invarianti]] — cose che NON vanno toccate senza rompere qualcosa
- [[scopo_e_gap]] — vero scopo commerciale, analisi multi-prospettiva, costi Google, roadmap

## Convenzioni

- Riferimenti al codice come `file.py:linea` (es. `app.py:105`).
- Se una scelta è stata **fatta e scartata**, va scritta in [[decisioni]] con il perché.
- Se un'invariante è documentata in un file di codice come commento, **non duplicarla qui**: linka.

## Stato

Wiki inizializzata: 2026-07-01. Il progetto è in produzione su Cloud Run (regione `europe-west1`).
