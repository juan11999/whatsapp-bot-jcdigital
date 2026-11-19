"""Language detection, handoff, LLM (optional) and rule-based fallback replies."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx

from config import (
    BUSINESS_NAME,
    HANDOFF_LOG,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_PROVIDER,
    PROVIDER_KEY_ENVS,
    load_knowledge,
)
from memory import get_history, handoff_count, record_handoff

log = logging.getLogger("whatsapp-bot.responder")

ZURICH = timezone(timedelta(hours=1))  # Europe/Zurich is UTC+1 in mid-November (CET)

LANGS = ("es", "fr", "en", "pt")

# Keyword scores are compact on purpose: enough signal for short WhatsApp lines.
_LANG_WORDS: dict[str, tuple[str, ...]] = {
    "es": (
        "hola", "buenas", "buenos", "gracias", "precio", "precios", "cuánto", "cuanto",
        "página", "pagina", "web", "quiero", "necesito", "información", "informacion",
        "estética", "estetica", "cita", "citas", "humano", "persona", "reunión",
        "reunion", "presupuesto", "hola,", "qué", "que", "cómo", "como", "por favor",
        "whatsapp", "plantillas", "servicios", "negocio", "ayuda", "llamada", "hablar",
        "ustedes", "vosotros", "buenos días", "buenas tardes", "buenas noches",
    ),
    "fr": (
        "bonjour", "bonsoir", "salut", "merci", "prix", "combien", "site", "page",
        "je voudrais", "j'aimerais", "besoin", "rendez-vous", "devis", "humain",
        "esthétique", "esthetique", "modèles", "modeles", "s'il", "vous", "nous",
        "appel", "réunion", "reunion", "parler", "aide", "services", "entreprise",
        "s'il vous plaît", "svp", "est-ce", "êtes", "etes",
    ),
    "en": (
        "hello", "hi", "hey", "thanks", "thank", "price", "prices", "how much",
        "website", "landing", "need", "please", "meeting", "call", "human",
        "templates", "beauty", "salon", "help", "services", "what", "your",
        "quote", "budget", "business", "whatsapp",
    ),
    "pt": (
        "olá", "ola", "oi", "obrigado", "obrigada", "preço", "preco", "quanto custa",
        "página", "pagina", "site", "preciso", "quero", "informação", "informacao",
        "estética", "estetica", "humano", "pessoa", "reunião", "reuniao", "orçamento",
        "orcamento", "por favor", "whatsapp", "modelos", "serviços", "servicos",
        "ajuda", "chamada", "falar", "vocês", "voces", "bom dia", "boa tarde",
    ),
}

_DIACRITICS = {
    "es": ("á", "é", "í", "ó", "ú", "ñ", "¿", "¡"),
    "fr": ("à", "â", "ç", "è", "é", "ê", "ë", "î", "ï", "ô", "ù", "û", "œ"),
    "pt": ("ã", "õ", "ç", "á", "â", "é", "ê", "ó", "ô", "í", "ú"),
    "en": (),
}

# --- intent ---------------------------------------------------------------

_HUMAN_RE = re.compile(
    r"\b("
    r"humano|humana|asesor|asesora|persona real|hablar con alguien|hablar con juan|"
    r"juan camilo|operador|agente\b|un humano|"
    r"humain|humaine|quelqu'un|parler (à|a|avec) (un |une )?(humain|personne|conseiller)|"
    r"human|real person|talk to (a |someone|juan)|speak (to|with) (a )?(human|person|agent|juan)|"
    r"pessoa real|falar com (algu[eé]m|humano|pessoa|juan)"
    r")\b",
    re.I,
)

_MEETING_RE = re.compile(
    r"("
    r"\b(reuni[oó]n|videollamada|rendez-?vous|r[eé]union|reuni[aã]o|meeting)\b|"
    r"\b(book a call|schedule (a )?(call|meeting)|appointment with)\b|"
    r"\b(agendar|concertar|pedir|quiero|queremos|podemos)\b.{0,40}"
    r"\b(cita|llamada|reuni[oó]n|call|meeting|rendez-?vous)\b|"
    r"\b(una |la )?(llamada|cita) (con |para )?(juan|ustedes|vosotros|usted|vos)\b|"
    r"\b(un appel|appeler|on peut (s.appeler|se parler))\b|"
    r"\b(marcar (uma )?(reuni[aã]o|chamada)|ligar (para|pra))\b|"
    r"\b(llamada con|llamadme|llámame|llame me)\b"
    r")",
    re.I,
)

_QUOTE_RE = re.compile(
    r"\b("
    r"presupuesto|cotizaci[oó]n|cotizar|presupuesto formal|"
    r"devis|proposition commerciale|"
    r"quote\b|quotation|formal (quote|proposal)|custom (price|quote)|"
    r"or[cç]amento"
    r")\b",
    re.I,
)

_PRICE_RE = re.compile(
    r"\b("
    r"precio|precios|cu[aá]nto (cuesta|vale|cobr|sale)|tarifa|coste|costo|"
    r"prix|combien ([cç]a )?co[uû]te|tarif|"
    r"price|prices|how much|cost\b|fee\b|"
    r"pre[cç]o|precos|quanto custa|valor\b"
    r")\b",
    re.I,
)

_GREET_RE = re.compile(
    r"^\s*(hola+|buenas?( tardes| d[ií]as| noches)?|hey+|hi+|hello+|holi|"
    r"bonjour|bonsoir|salut|coucou|ol[aá]|oi+|bom dia|boa (tarde|noite)"
    r")[\s!?.]*$",
    re.I,
)

_THANKS_RE = re.compile(
    r"\b(gracias|muchas gracias|merci|thank(s| you)|obrigad[oa])\b",
    re.I,
)

_WEB_RE = re.compile(
    r"\b(p[aá]gina(?:s)? web|landing|sitio web|web\b|website|site (web|internet)|site\b)\b",
    re.I,
)

_WA_RE = re.compile(
    r"\b(whats?app|wsp|guasap|chatbot|bot de whats?app|ia en whats?app)\b",
    re.I,
)

_AI_RE = re.compile(
    r"\b(inteligencia artificial|\bia\b|ai\b|chatbot|automatizaci[oó]n|automations?|"
    r"automation|automatisation)\b",
    re.I,
)

_TMPL_RE = re.compile(
    r"\b("
    r"plantilla|plantillas|est[eé]tica|esteticas|sal[oó]n|salones|"
    r"recordatorio|promoci[oó]n|rese[nñ]a|seguimiento|"
    r"mod[eè]le|modeles|esth[eé]tique|institut|"
    r"template|templates|beauty (salon|centre|center)|aesthetics|"
    r"modelo|modelos|cl[ií]nica de est[eé]tica"
    r")\b",
    re.I,
)

_SERVICES_RE = re.compile(
    r"\b(servicios?|qu[eé] hac[eé]is|qu[eé] ofrecen|qu[eé] hacen|"
    r"services?|que proposez|vous faites|"
    r"what (do you|can you)|offerings?|"
    r"servi[cç]os|o que (voc[eê]s )?fazem)\b",
    re.I,
)


def detect_language(text: str, fallback: str = "es") -> str:
    if not text or not text.strip():
        return fallback
    lowered = text.lower()
    scores = {lang: 0.0 for lang in LANGS}
    for lang, words in _LANG_WORDS.items():
        for w in words:
            if w in lowered:
                scores[lang] += 1.5 if " " in w else 1.0
        for ch in _DIACRITICS[lang]:
            if ch in lowered:
                scores[lang] += 0.35
    # Unique markers
    if re.search(r"[¿¡ñ]", lowered):
        scores["es"] += 2
    if re.search(r"\b(ç|œ)\b", lowered) or "où" in lowered or "êtes" in lowered:
        scores["fr"] += 1.5
    if re.search(r"[ãõ]", lowered):
        scores["pt"] += 2
    best = max(scores, key=scores.get)
    if scores[best] <= 0:
        return fallback
    return best


def is_handoff_intent(text: str) -> tuple[bool, str]:
    """Return (triggered, reason). Price *questions* are not a handoff by themselves;
    a formal quote / meeting / human request is."""
    if not text:
        return False, ""
    if _HUMAN_RE.search(text):
        return True, "human"
    if _MEETING_RE.search(text):
        return True, "meeting"
    if _QUOTE_RE.search(text):
        return True, "quote"
    return False, ""


def log_handoff(user_id: str, text: str, reason: str, lang: str, count: int) -> None:
    HANDOFF_LOG.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(ZURICH).strftime("%Y-%m-%d %H:%M:%S %Z (Europe/Zurich)")
    line = (
        f"{now} | user={user_id} | reason={reason} | count={count} | lang={lang} | "
        f"message={text.replace(chr(10), ' ')}\n"
    )
    with HANDOFF_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)
    log.info("Handoff logged for %s (%s, count=%s)", user_id, reason, count)


# --- copy ----------------------------------------------------------------

ASK_TEXT = {
    "es": "Para poder ayudarte mejor, ¿puedes escribir tu consulta en un mensaje de texto?",
    "fr": "Pour mieux t'aider, peux-tu écrire ta question dans un message texte ?",
    "en": "To help you better, could you write your question as a text message?",
    "pt": "Para te ajudar melhor, podes escrever a tua pergunta num mensagem de texto?",
}

HANDOFF_MSG = {
    "es": (
        "Perfecto. Juan Camilo te contactará en breve para hablarlo contigo. "
        "¡Gracias por escribir a JC Digital Geneve!"
    ),
    "fr": (
        "Parfait. Juan Camilo te recontactera sous peu pour en parler avec toi. "
        "Merci d'avoir écrit à JC Digital Geneve !"
    ),
    "en": (
        "Perfect. Juan Camilo will contact you shortly to talk it through. "
        "Thanks for writing to JC Digital Geneve!"
    ),
    "pt": (
        "Perfeito. O Juan Camilo entra em contacto em breve para falar contigo. "
        "Obrigado por escreveres à JC Digital Geneve!"
    ),
}

# If they asked for a price quote, include orientative package before the handoff close.
HANDOFF_PRICE_EXTRA = {
    "es": (
        "Como referencia, el paquete de chatbot de WhatsApp es de CHF 450 de alta + CHF 89/mes "
        "(precios orientativos, a confirmar por Juan Camilo). "
    ),
    "fr": (
        "À titre indicatif, le pack chatbot WhatsApp est de CHF 450 de mise en place + CHF 89/mois "
        "(prix indicatifs, à confirmer par Juan Camilo). "
    ),
    "en": (
        "As a guide, the WhatsApp chatbot package is CHF 450 setup + CHF 89/month "
        "(indicative prices, to be confirmed by Juan Camilo). "
    ),
    "pt": (
        "Como referência, o pack de chatbot WhatsApp é de CHF 450 de setup + CHF 89/mês "
        "(preços orientativos, a confirmar pelo Juan Camilo). "
    ),
}


def _t(table: dict[str, str], lang: str) -> str:
    return table.get(lang) or table["es"]


def fallback_reply(text: str, lang: str, *, non_text: bool = False, handoff_reason: str | None = None) -> str:
    if non_text:
        return _t(ASK_TEXT, lang)

    if handoff_reason:
        extra = _t(HANDOFF_PRICE_EXTRA, lang) if handoff_reason == "quote" or (
            handoff_reason == "meeting" and text and _PRICE_RE.search(text)
        ) else ""
        return (extra + _t(HANDOFF_MSG, lang)).strip()

    if text and _GREET_RE.match(text.strip()):
        return {
            "es": (
                f"¡Hola! Soy el asistente de {BUSINESS_NAME}. Ayudamos a pequeños negocios con "
                "IA, páginas web, WhatsApp Business y plantillas para centros de estética. "
                "¿En qué te puedo ayudar?"
            ),
            "fr": (
                f"Bonjour ! Je suis l'assistant de {BUSINESS_NAME}. Nous aidons les petites "
                "entreprises avec l'IA, les sites web, WhatsApp Business et des modèles pour "
                "les instituts d'esthétique. Comment puis-je t'aider ?"
            ),
            "en": (
                f"Hi! I'm the assistant for {BUSINESS_NAME}. We help small businesses with AI, "
                "websites, WhatsApp Business and ready-made templates for beauty salons. "
                "How can I help?"
            ),
            "pt": (
                f"Olá! Sou o assistente da {BUSINESS_NAME}. Ajudamos pequenos negócios com IA, "
                "sites, WhatsApp Business e modelos prontos para centros de estética. "
                "Em que posso ajudar?"
            ),
        }[lang if lang in LANGS else "es"]

    if text and _THANKS_RE.search(text) and len(text) < 80 and not _PRICE_RE.search(text):
        return {
            "es": "¡Con gusto! Si quieres, Juan Camilo puede llamarte para concretar una propuesta.",
            "fr": "Avec plaisir ! Si tu veux, Juan Camilo peut t'appeler pour préciser une proposition.",
            "en": "You're welcome! If you'd like, Juan Camilo can call you to put a proposal together.",
            "pt": "Com gosto! Se quiseres, o Juan Camilo pode ligar-te para concretizar uma proposta.",
        }[lang if lang in LANGS else "es"]

    bits: list[str] = []

    if text and _TMPL_RE.search(text):
        bits.append(
            {
                "es": (
                    "Tenemos plantillas listas para centros de estética y salones: recordatorios de cita, "
                    "promociones, seguimiento post-tratamiento, petición de reseñas y mensajes para "
                    "Instagram y WhatsApp, adaptables al tono de cada centro."
                ),
                "fr": (
                    "Nous proposons des modèles prêts à l'emploi pour instituts d'esthétique et salons : "
                    "rappels de rendez-vous, promotions, suivi post-soin, demande d'avis et messages "
                    "pour Instagram et WhatsApp, adaptés au ton de chaque établissement."
                ),
                "en": (
                    "We have ready-made templates for beauty salons and aesthetics centres: appointment "
                    "reminders, promotions, post-treatment follow-ups, review requests, and Instagram "
                    "and WhatsApp message templates, adapted to each studio's tone."
                ),
                "pt": (
                    "Temos modelos prontos para centros de estética e salões: lembretes de consulta, "
                    "promoções, acompanhamento pós-tratamento, pedido de avaliações e mensagens para "
                    "Instagram e WhatsApp, adaptáveis ao tom de cada centro."
                ),
            }[lang if lang in LANGS else "es"]
        )

    if text and _WA_RE.search(text):
        bits.append(
            {
                "es": (
                    "Montamos WhatsApp Business y un chatbot de IA. Tú puedes seguir contestando "
                    "desde el móvil (modo coexistencia) y el bot atiende lo que acordemos."
                ),
                "fr": (
                    "Nous mettons en place WhatsApp Business et un chatbot IA. Tu peux continuer "
                    "à répondre depuis le téléphone (mode coexistence) et le bot s'occupe de ce que nous convenons."
                ),
                "en": (
                    "We set up WhatsApp Business and an AI chatbot. You can keep replying from your "
                    "phone (coexistence mode) while the bot handles what we agree."
                ),
                "pt": (
                    "Configuramos o WhatsApp Business e um chatbot de IA. Podes continuar a responder "
                    "no telemóvel (modo coexistência) e o bot trata do que acordarmos."
                ),
            }[lang if lang in LANGS else "es"]
        )

    if text and _WEB_RE.search(text):
        bits.append(
            {
                "es": (
                    "También diseñamos páginas web y landing pages claras y rápidas, pensadas para "
                    "el móvil. No publicamos un precio cerrado: Juan Camilo te prepara una propuesta a medida."
                ),
                "fr": (
                    "Nous concevons aussi des sites et landing pages clairs et rapides, pensés pour "
                    "le mobile. Il n'y a pas de tarif fixe publié : Juan Camilo prépare une proposition sur mesure."
                ),
                "en": (
                    "We also design clear, fast websites and landing pages, built for mobile. We don't "
                    "publish a fixed price; Juan Camilo will prepare a tailored proposal."
                ),
                "pt": (
                    "Também criamos sites e landing pages claros e rápidos, pensados para telemóvel. "
                    "Não há um preço fechado publicado: o Juan Camilo prepara uma proposta à medida."
                ),
            }[lang if lang in LANGS else "es"]
        )

    if text and _AI_RE.search(text) and not _WA_RE.search(text):
        bits.append(
            {
                "es": (
                    "Hacemos soluciones de IA para negocios: chatbots de atención, automatizaciones "
                    "(recordatorios, seguimiento, FAQs) e integración en procesos que ya uses."
                ),
                "fr": (
                    "Nous proposons des solutions d'IA pour les entreprises : chatbots, automatisations "
                    "(rappels, suivi, FAQ) et intégration dans tes processus existants."
                ),
                "en": (
                    "We build AI solutions for businesses: support chatbots, automations "
                    "(reminders, follow-up, FAQs) and integration into the tools you already use."
                ),
                "pt": (
                    "Fazemos soluções de IA para negócios: chatbots de atendimento, automações "
                    "(lembretes, acompanhamento, FAQs) e integração nos processos que já usas."
                ),
            }[lang if lang in LANGS else "es"]
        )

    if text and _SERVICES_RE.search(text) and not bits:
        bits.append(
            {
                "es": (
                    f"En {BUSINESS_NAME} ofrecemos: 1) soluciones de IA (chatbots y automatizaciones), "
                    "2) páginas web y landings, 3) alta de WhatsApp Business con chatbot, y 4) plantillas "
                    "para centros de estética (citas, promociones, seguimiento, reseñas, Instagram/WhatsApp)."
                ),
                "fr": (
                    f"Chez {BUSINESS_NAME} nous proposons : 1) des solutions d'IA (chatbots et automatisations), "
                    "2) des sites et landing pages, 3) WhatsApp Business avec chatbot, et 4) des modèles pour "
                    "instituts d'esthétique (rdv, promotions, suivi, avis, Instagram/WhatsApp)."
                ),
                "en": (
                    f"At {BUSINESS_NAME} we offer: 1) AI solutions (chatbots and automations), "
                    "2) websites and landing pages, 3) WhatsApp Business setup with an AI chatbot, and "
                    "4) templates for beauty salons (appointments, promotions, follow-ups, reviews, Instagram/WhatsApp)."
                ),
                "pt": (
                    f"Na {BUSINESS_NAME} oferecemos: 1) soluções de IA (chatbots e automações), "
                    "2) sites e landing pages, 3) configuração de WhatsApp Business com chatbot, e "
                    "4) modelos para centros de estética (consultas, promoções, acompanhamento, avaliações, Instagram/WhatsApp)."
                ),
            }[lang if lang in LANGS else "es"]
        )

    if text and _PRICE_RE.search(text):
        asked_web_only = bool(_WEB_RE.search(text) and not _WA_RE.search(text) and not _AI_RE.search(text))
        if asked_web_only:
            # The website blurb already says there is no published price.
            if not bits:
                bits.append(
                    {
                        "es": "Para una página web no hay un precio cerrado publicado: Juan Camilo te prepara una propuesta a medida.",
                        "fr": "Pour un site web il n'y a pas de tarif fixe publié : Juan Camilo prépare une proposition sur mesure.",
                        "en": "We don't publish a fixed website price; Juan Camilo will prepare a tailored proposal.",
                        "pt": "Para um site não há um preço fechado publicado: o Juan Camilo prepara uma proposta à medida.",
                    }[lang if lang in LANGS else "es"]
                )
        else:
            bits.append(
            {
                "es": (
                    "Para un chatbot de WhatsApp, el paquete orientativo es de CHF 450 de alta + CHF 89/mes "
                    "(precios orientativos, a confirmar por Juan Camilo). "
                    "Para webs u otros servicios no hay un precio publicado: te lo confirma él en una llamada."
                ),
                "fr": (
                    "Pour un chatbot WhatsApp, le pack indicatif est de CHF 450 de mise en place + CHF 89/mois "
                    "(prix indicatifs, à confirmer par Juan Camilo). "
                    "Pour un site ou d'autres services, il n'y a pas de tarif publié : il te le confirmera lors d'un appel."
                ),
                "en": (
                    "For a WhatsApp chatbot, the indicative package is CHF 450 setup + CHF 89/month "
                    "(indicative prices, to be confirmed by Juan Camilo). "
                    "We don't publish a fixed price for websites or other services; he'll confirm it on a call."
                ),
                "pt": (
                    "Para um chatbot de WhatsApp, o pack orientativo é de CHF 450 de setup + CHF 89/mês "
                    "(preços orientativos, a confirmar pelo Juan Camilo). "
                    "Para sites ou outros serviços não há preço publicado: ele confirma numa chamada."
                ),
            }[lang if lang in LANGS else "es"]
        )

    if bits:
        close = {
            "es": " Si quieres, Juan Camilo te contacta para concretarlo. ¿Te va bien una llamada?",
            "fr": " Si tu veux, Juan Camilo te recontacte pour le préciser. Un appel te convient ?",
            "en": " If you'd like, Juan Camilo can contact you to go over it. Would a call work?",
            "pt": " Se quiseres, o Juan Camilo contacta-te para concretizar. Serve uma chamada?",
        }[lang if lang in LANGS else "es"]
        return " ".join(bits) + close

    return {
        "es": (
            f"Gracias por tu mensaje. En {BUSINESS_NAME} ayudamos con IA, webs, WhatsApp Business "
            "y plantillas para estéticas. Cuéntame un poco más, o si prefieres, Juan Camilo te contacta en breve."
        ),
        "fr": (
            f"Merci pour ton message. Chez {BUSINESS_NAME} nous aidons avec l'IA, les sites, WhatsApp Business "
            "et des modèles pour instituts. Dis-m'en un peu plus, ou Juan Camilo peut te recontacter sous peu."
        ),
        "en": (
            f"Thanks for your message. At {BUSINESS_NAME} we help with AI, websites, WhatsApp Business "
            "and salon templates. Tell me a bit more, or if you prefer, Juan Camilo will contact you shortly."
        ),
        "pt": (
            f"Obrigado pela mensagem. Na {BUSINESS_NAME} ajudamos com IA, sites, WhatsApp Business "
            "e modelos para estéticas. Conta-me um pouco mais, ou se preferires o Juan Camilo contacta-te em breve."
        ),
    }[lang if lang in LANGS else "es"]


# --- LLM -----------------------------------------------------------------

_OPENAI_COMPAT = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY", "grok-4-fast"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai/gpt-4o-mini"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-small-latest"),
    "ollama": ("http://127.0.0.1:11434/v1", "", "llama3.2"),
}

# Gemini and Anthropic use different request shapes; we still support them.


def _first_present_key() -> tuple[str, str]:
    """Return (provider, key) without exposing the value. LLM_API_KEY wins if set."""
    if LLM_API_KEY:
        return (LLM_PROVIDER or "openai"), LLM_API_KEY
    if LLM_PROVIDER:
        env_name = PROVIDER_KEY_ENVS.get(LLM_PROVIDER, "")
        if LLM_PROVIDER == "ollama":
            return "ollama", ""
        if env_name and (os.environ.get(env_name) or "").strip():
            return LLM_PROVIDER, os.environ[env_name].strip()
        if LLM_PROVIDER == "gemini" and (os.environ.get("GOOGLE_API_KEY") or "").strip():
            return "gemini", os.environ["GOOGLE_API_KEY"].strip()
    # Auto-detect first available known key
    order = ("openai", "anthropic", "xai", "groq", "openrouter", "gemini", "google", "together", "mistral")
    for prov in order:
        env_name = PROVIDER_KEY_ENVS.get(prov, "")
        val = (os.environ.get(env_name) or "").strip() if env_name else ""
        if val:
            return ("gemini" if prov == "google" else prov), val
    return "", ""


def llm_status() -> dict[str, Any]:
    prov, key = _first_present_key()
    return {
        "provider": prov or None,
        "has_key": bool(key) or prov == "ollama",
        "model": LLM_MODEL or None,
        "mode": "llm" if (prov and (key or prov == "ollama")) else "fallback",
    }


def _system_prompt(lang: str) -> str:
    knowledge = load_knowledge()
    lang_name = {"es": "Spanish", "fr": "French", "en": "English", "pt": "Portuguese"}.get(lang, "Spanish")
    return (
        f"You are the WhatsApp assistant of {BUSINESS_NAME}, speaking as a helpful business assistant "
        f"(not as Juan Camilo himself). Always reply in {lang_name}.\n\n"
        "Follow the knowledge file strictly. Never invent prices, discounts, delivery dates or promises "
        "that are not written there. The only prices you may quote are the WhatsApp chatbot package "
        "CHF 450 setup + CHF 89/month, always labelled as indicative and to be confirmed by Juan Camilo.\n"
        "Be warm, professional and concise (usually 2–5 short sentences). Use emoji sparingly or not at all.\n"
        "If the user wants a human, a meeting or a formal quote, say that Juan Camilo will contact them shortly.\n"
        "If you are unsure, say so and offer a call with Juan Camilo.\n\n"
        f"KNOWLEDGE FILE:\n{knowledge}"
    )


async def _chat_openai_compat(base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> str | None:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={"model": model, "messages": messages, "temperature": 0.4, "max_tokens": 400},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _chat_anthropic(api_key: str, model: str, system: str, messages: list[dict[str, str]]) -> str | None:
    model = model or "claude-3-5-haiku-latest"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 400,
                "system": system,
                "messages": messages,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        parts = [p.get("text", "") for p in data.get("content", []) if p.get("type") == "text"]
        return "\n".join(parts).strip() or None


async def _chat_gemini(api_key: str, model: str, system: str, messages: list[dict[str, str]]) -> str | None:
    model = model or "gemini-2.0-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url,
            params={"key": api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 400},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def llm_reply(user_id: str, text: str, lang: str) -> str | None:
    prov, key = _first_present_key()
    if not prov or (not key and prov != "ollama"):
        return None
    system = _system_prompt(lang)
    history = get_history(user_id)
    messages: list[dict[str, str]] = []
    for turn in history:
        role = "assistant" if turn["role"] == "assistant" else "user"
        messages.append({"role": role, "content": turn["content"]})
    # Ensure the latest user turn is present (it may already have been stored).
    if not messages or messages[-1]["role"] != "user" or messages[-1]["content"] != text:
        messages.append({"role": "user", "content": text})
    try:
        if prov == "anthropic":
            chat_msgs = [m for m in messages if m["role"] in {"user", "assistant"}]
            return await _chat_anthropic(key, LLM_MODEL, system, chat_msgs)
        if prov in {"gemini", "google"}:
            return await _chat_gemini(key, LLM_MODEL, system, messages)
        base, _env, default_model = _OPENAI_COMPAT.get(prov, ("https://api.openai.com/v1", "", "gpt-4o-mini"))
        if LLM_BASE_URL:
            base = LLM_BASE_URL
        model = LLM_MODEL or default_model
        full = [{"role": "system", "content": system}, *messages]
        return await _chat_openai_compat(base, key, model, full)
    except Exception:
        log.exception("LLM call failed; falling back to rules")
        return None


async def generate_reply(
    user_id: str,
    text: str | None,
    lang_hint: str | None = None,
    *,
    message_type: str = "text",
    force_fallback: bool = False,
) -> dict[str, Any]:
    """Build the outbound reply and say whether this turn is a handoff / mute."""
    non_text = not (text and text.strip()) or (message_type not in {"text", "button", "button_reply", "list_reply", "interactive"})
    # Image with caption: treat caption as text
    if message_type == "image" and text:
        non_text = False

    lang = detect_language(text or "", fallback=lang_hint or "es") if not non_text else (lang_hint or "es")
    reason = ""
    handoff = False

    if not non_text:
        handoff, reason = is_handoff_intent(text or "")

    if handoff:
        count = record_handoff(user_id)
        log_handoff(user_id, text or "", reason, lang, count)
        # First handoff: send the short confirmation. Later "humano" / quote / meeting: stay silent.
        if count >= 2:
            return {
                "text": None,
                "lang": lang,
                "handoff": True,
                "handoff_count": count,
                "silent": True,
                "reason": "already_handed_off",
                "source": "handoff",
            }
        body = fallback_reply(text or "", lang, handoff_reason=reason)
        return {
            "text": body,
            "lang": lang,
            "handoff": True,
            "handoff_count": count,
            "silent": False,
            "reason": reason,
            "source": "handoff",
        }

    if non_text:
        return {
            "text": fallback_reply("", lang, non_text=True),
            "lang": lang,
            "handoff": False,
            "silent": False,
            "reason": "non_text",
            "source": "fallback",
        }

    source = "fallback"
    body = None
    if not force_fallback:
        body = await llm_reply(user_id, text or "", lang)
        if body:
            source = "llm"
    if not body:
        body = fallback_reply(text or "", lang)

    return {
        "text": body,
        "lang": lang,
        "handoff": False,
        "silent": False,
        "reason": "",
        "source": source,
    }


def cheap_llm_options() -> list[str]:
    return [
        "Groq (free tier, OpenAI-compatible, Llama/Mixtral — good default to try first)",
        "Google Gemini (free tier via GEMINI_API_KEY or GOOGLE_API_KEY, e.g. gemini-2.0-flash)",
        "OpenRouter (some free models; set OPENROUTER_API_KEY and LLM_PROVIDER=openrouter)",
        "xAI / Grok (XAI_API_KEY, OpenAI-compatible)",
        "OpenAI gpt-4o-mini or similar (cheap, not free)",
        "Ollama on this box later (LLM_PROVIDER=ollama) if a local model is installed — no API bill",
    ]
