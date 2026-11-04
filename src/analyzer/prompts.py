"""Definizione dei prompt e degli schemi di analisi per Gemini.
Questo modulo è progettato per essere facilmente esteso quando l'utente vorrà
configurare ulteriori analisi nel dettaglio (es. argomenti, riassunti, tag, codice, ecc.).
"""

from typing import Any, Dict
from pydantic import BaseModel, Field


class PostAnalysisResult(BaseModel):
    """Schema strutturato dell'analisi del post, allineato ai campi mostrati sul sito."""

    autore: str = Field(
        description="L'autore del post nel formato 'Nome (@username)'."
    )
    titolo: str = Field(
        description="Un titolo sintetico, chiaro, accattivante ed esplicativo del post (massimo 12-15 parole) che riassuma accuratamente l'argomento centrale o l'intuizione chiave."
    )
    categoria: str = Field(
        default="📌 Risorsa X",
        description="Una delle categorie del sito.",
    )
    il_succo: str = Field(default="", description="Sintesi in 1-2 frasi.")
    punti_chiave: list[str] = Field(default_factory=list, description="3-4 punti salienti.")
    caso_d_uso: str = Field(default="", description="Per chi è utile, in una frase.")
    tag: list[str] = Field(default_factory=list, description="3-5 hashtag con #.")


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
2. Un titolo sintetico del post (massimo 10-15 parole, in lingua italiana)
3. 'categoria': esattamente una tra "🤖 Agenti & Coding AI", "💡 Prompt & Guide", "🛠️ Strumenti & App", "🔬 Modelli & Ricerca", "⚡ Risorse & GitHub", "📰 News & Trend"
4. 'il_succo': sintesi concreta in 1-2 frasi, senza hype
5. 'punti_chiave': 3 o 4 punti salienti
6. 'caso_d_uso': una frase su quando è utile
7. 'tag': 3-5 hashtag minuscoli con #

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
  "titolo": "Titolo sintetico ed esplicativo in italiano",
  "categoria": "🛠️ Strumenti & App",
  "il_succo": "Sintesi in una o due frasi.",
  "punti_chiave": ["Punto 1", "Punto 2", "Punto 3"],
  "caso_d_uso": "Utile per chi deve fare X.",
  "tag": ["#ai", "#strumenti"]
}
Non aggiungere spiegazioni, blocchi di codice markdown con ```json, solo il JSON puro.
"""
    return prompt
