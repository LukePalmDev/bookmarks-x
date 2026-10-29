"""Definizione dei prompt e degli schemi di analisi per Gemini.
Questo modulo è progettato per essere facilmente esteso quando l'utente vorrà
configurare ulteriori analisi nel dettaglio (es. argomenti, riassunti, tag, codice, ecc.).
"""

from typing import Any, Dict
from pydantic import BaseModel, Field


class PostAnalysisResult(BaseModel):
    """Schema strutturato dell'analisi del post."""

    autore: str = Field(
        description="L'autore del post nel formato 'Nome (@username)'."
    )
    titolo: str = Field(
        description="Un titolo sintetico, chiaro, accattivante ed esplicativo del post (massimo 12-15 parole) che riassuma accuratamente l'argomento centrale o l'intuizione chiave."
    )


def build_analysis_prompt(tweet_data: Dict[str, Any]) -> str:
    """Costruisce il prompt per Gemini.
    Attualmente estrae in automatico l'Autore e il Titolo del post,
    come richiesto inizialmente dall'utente.
    """
    author = f"{tweet_data.get('author_name', '')} (@{tweet_data.get('author_screen_name', '')})"
    text = tweet_data.get("tweet_text", "").strip()
    quoted = tweet_data.get("quoted_status")
    if isinstance(quoted, str):
        try:
            import json
            quoted = json.loads(quoted)
        except Exception:
            quoted = None

    prompt = f"""Sei un assistente editoriale esperto nell'analisi di contenuti tecnici, scientifici e social da X (Twitter).

Il tuo compito attuale è analizzare il seguente post salvato (bookmark) ed estrarre con precisione:
1. L'autore del post nel formato: 'Nome (@handle)'
2. Un titolo sintetico del post (massimo 10-15 parole, in lingua italiana): deve essere chiaro, informativo, accurato e rappresentare la sintesi del contenuto del tweet, come se fosse l'intestazione di un articolo o di una nota archivio.

--- DETTAGLI DEL POST ---
Autore originale: {author}
Testo:
{text}
"""

    if quoted:
        prompt += f"""
Tweet citato da @{quoted.get('author')}:
{quoted.get('text')}
"""

    prompt += """
--- ISTRUZIONI DI RISPOSTA ---
Rispondi ESCLUSIVAMENTE con un oggetto JSON valido con la seguente struttura:
{
  "autore": "Nome (@username)",
  "titolo": "Titolo sintetico ed esplicativo in italiano"
}
Non aggiungere spiegazioni, blocchi di codice markdown con ```json, solo il JSON puro.
"""
    return prompt
