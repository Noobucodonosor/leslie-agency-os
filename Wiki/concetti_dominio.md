# Concetti di dominio

Questo file traduce le parole tecniche del codice nel linguaggio del business (CIBO Marine — flotta yacht di lusso a Portofino).

Se un termine appare nel codice ma non è ovvio cosa significhi commercialmente, cercalo qui.

## L'obiettivo commerciale

CIBO Marine vende **charter yacht di lusso** partendo da Portofino. Non cerca clienti finali (troppo pochi, troppo dispersi). Cerca **partner B2B** che hanno già clienti UHNWI e possono raccomandare il servizio.

Ogni "lead" nel DB è **un candidato partner B2B**, non un cliente finale.

## Glossario

### HNWI / UHNWI
- **HNWI**: High Net Worth Individual, patrimonio > 1M$
- **UHNWI**: Ultra HNWI, patrimonio > 30M$

Non sono clienti diretti. Sono i clienti **dei partner** che cerchiamo (concierge, hotel 5*, family office).

### Lead
Un'azienda B2B trovata via Google Places, con sito web valido. Salvata nella tabella `leads` di [[schema_db]].

### Lead premium (`is_premium=1`)
Lead con score AI ≥ 7. Sono i partner che hanno **statisticamente** la clientela giusta.
Ricevono un audit approfondito automatico. Vedi [[flusso_scout]].

### Score (0-10)
Assegnato da `analyze_spending_capacity()` in [[ai_analyzer]]. Il prompt lo istruisce a essere severo.
- **8-10**: excellent fit (hotel 5* luxury, concierge internazionali, family office)
- **5-7**: possible fit ma non ideale
- **0-4**: scarta

### Nicchia (`niche_key`)
Etichetta breve estratta dall'audit AI. Esempi (ipotetici, dipendono da cosa l'AI restituisce):
- "UHNWI Concierge Milano"
- "Boutique Luxury DMC"
- "Silicon Valley Travel Designer"
- "Wedding Planner Portofino"

Registrata in `learned_intelligence` con `confidence`. Più lead premium ricadono nella stessa nicchia, più `confidence` cresce. Le query associate rientrano nel pool. Vedi [[query_pools]].

### Selling angle
Suggerimento AI di **come** approcciare il lead. Max 20-25 parole. Esempio di stile atteso:
> "Proporre pacchetti charter privati per la loro clientela VIP durante la stagione estiva, con focus su esclusività e curated experience."

Salvato in `leads.selling_angle` e mostrato in UI accanto al lead.

### Client type
Etichetta 2-4 parole che descrive **cosa fa** il lead. Esempi: "UHNWI Concierge", "Boutique Luxury DMC", "5-Star Hotel Concierge Desk".

Non è persistito nel DB (solo yielded per la UI live durante lo scout).

### Market saturation
Un numero 10-60. **Attualmente random**: `random.randint(10, 45)` in `app.py:1124` e `random.randint(20, 60)` in `autonomous_scout.py:242`. Vedi [[decisioni]] e [[invarianti]] — è un placeholder che aspetta un vero calcolo (es. quante nicchie simili, quanti concorrenti trovati).

### Query esaurita
Query che ha già dato tutti i suoi frutti. Google Places restituisce risultati ma **tutti** sono già nel DB.
Il generator di query in [[query_pools]] la filtra fuori automaticamente.

### Pesi (weights)
Interi 0-100 per categoria. Determinano la **distribuzione** delle query nel batch. Peso 0 = disattivata, peso ≥80 = candidata a espansione AI. Vedi [[query_pools]].

## Tabella "pretende di essere" vs "è davvero"

| Nel codice si chiama | Nel business è | Note |
|---|---|---|
| `status='saved'` | Lead attivo | Unico stato usato oggi — vedi [[invarianti]] |
| `notes` | Il testo dell'audit AI | Non sono note manuali — sono AI-generated |
| `email` | CSV di email trovate | Potrebbero essere info@ non decisori |
| `phone` | Telefono azienda | Da Google Places, non verificato |
| `seo_data` | Snapshot SEO al momento dell'audit | Non ricalcolato in tempo reale |

## Categorie di partner (chi cerchiamo)

Vedi [[query_pools]] per la lista completa. Semanticamente:

1. **Chi ha già i clienti a bordo di un hotel** → hotel 5*, resort lusso
2. **Chi organizza esperienze in Italia per stranieri** → DMC, incoming operator
3. **Chi ha ufficio all'estero e vende Italia** → travel agency US/UK Italy specialist
4. **Chi ha bisogno di barche come componente evento** → wedding planner, corporate event
5. **Chi già lavora con barche** → charter broker, yacht rental (concorrenza collaborativa)
6. **Chi porta gruppi corporate** → MICE agency
7. **Chi gestisce l'agenda dell'UHNWI** → concierge, lifestyle manager, family office
