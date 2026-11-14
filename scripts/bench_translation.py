"""Benchmark the local OPUS-MT translation packs used by VoiceType.

The script reports pack load time, CPU translation latency, memory, and
reference coverage on a fixed set of 20 dictation phrases, so the numbers in the
README are reproducible. Reference coverage counts how many content words,
digits, and Chinese characters of a human translation the pack kept; it is not
BLEU, and it measures the pack stage, the Whisper stage separately.

Examples::

    python scripts/bench_translation.py
    python scripts/bench_translation.py --targets ru,de --repeat 3
    python scripts/bench_translation.py --output bench-translation.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import platform
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voicetype import __version__, translation  # noqa: E402

# Realistic push-to-talk sentences: names, numbers, dates, instructions: the
# cases where dictation users notice translation quality first.
PHRASES: tuple[str, ...] = (
    "The meeting starts at nine in the morning.",
    "Please send the report to Anna before Friday.",
    "I will call you back in ten minutes.",
    "The train to Berlin leaves from platform four.",
    "My phone number is 0176 5532 1188.",
    "We need to sign the contract before the end of the month.",
    "The temperature outside is minus three degrees.",
    "Turn left at the next traffic light and continue for two kilometers.",
    "The invoice for the printer is 245 euros.",
    "Can you check whether the server was restarted last night?",
    "The project deadline is the twelfth of March.",
    "This feature should work offline, without any network connection.",
    "She arrived home late and nobody heard her.",
    "Open the settings window and enable translation.",
    "The old battery does not last longer than two hours.",
    "Everyone in the office prefers a shorter meeting.",
    "If the microphone is muted, no audio is recorded.",
    "The package was delivered on the third of June.",
    "I forgot to bring my glasses, so the text is blurry.",
    "He said the update takes about fifteen minutes.",
)

# Human reference translations for the phrases above. English is produced by
# Whisper itself, so it has no reference: the pack is the stage under test.
REFERENCES: dict[str, tuple[str, ...]] = {
    "ru": (
        "Встреча начинается в девять часов утра.",
        "Пожалуйста, отправь отчёт Анне до пятницы.",
        "Я перезвоню тебе через десять минут.",
        "Поезд на Берлин отправляется с четвёртой платформы.",
        "Мой номер телефона 0176 5532 1188.",
        "Мы должны подписать договор до конца месяца.",
        "Температура снаружи минус три градуса.",
        "Поверни на следующем светофоре налево и продолжай два километра.",
        "Счёт за принтер 245 евро.",
        "Можешь проверить, был ли сервер перезапущен прошлой ночью?",
        "Срок проекта двенадцатое марта.",
        "Эта функция должна работать офлайн, без сетевого подключения.",
        "Она пришла домой поздно, и никто её не услышал.",
        "Открой окно настроек и включи перевод.",
        "Старый аккумулятор держит не больше двух часов.",
        "Всем в офисе нравится более короткая встреча.",
        "Если микрофон выключен, аудио не записывается.",
        "Посылку доставили третьего июня.",
        "Я забыл очки, поэтому текст нечёткий.",
        "Он сказал, что обновление занимает примерно пятнадцать минут.",
    ),
    "de": (
        "Das Treffen beginnt um neun Uhr morgens.",
        "Bitte sende den Bericht bis Freitag an Anna.",
        "Ich rufe dich in zehn Minuten zurück.",
        "Der Zug nach Berlin fährt von Gleis vier ab.",
        "Meine Telefonnummer ist 0176 5532 1188.",
        "Wir müssen den Vertrag bis Ende des Monats unterschreiben.",
        "Die Außentemperatur ist minus drei Grad.",
        "Biegen Sie an der nächsten Ampel links ab und fahren Sie zwei Kilometer weiter.",
        "Die Rechnung für den Drucker beträgt 245 Euro.",
        "Können Sie prüfen, ob der Server letzte Nacht neu gestartet wurde?",
        "Die Projektfrist ist der zwölfte März.",
        "Diese Funktion sollte offline funktionieren, ohne Netzwerkverbindung.",
        "Sie kam spät nach Hause, und niemand hat sie gehört.",
        "Öffnen Sie das Einstellungsfenster und aktivieren Sie die Übersetzung.",
        "Der alte Akku hält nicht länger als zwei Stunden.",
        "Alle im Büro bevorzugen ein kürzeres Meeting.",
        "Wenn das Mikrofon stummgeschaltet ist, wird kein Audio aufgenommen.",
        "Das Paket wurde am dritten Juni geliefert.",
        "Ich habe meine Brille vergessen, deshalb ist der Text unscharf.",
        "Er sagte, das Update dauert etwa fünfzehn Minuten.",
    ),
    "fr": (
        "La réunion commence à neuf heures du matin.",
        "Merci d'envoyer le rapport à Anna avant vendredi.",
        "Je te rappellerai dans dix minutes.",
        "Le train pour Berlin part de la voie quatre.",
        "Mon numéro de téléphone est le 0176 5532 1188.",
        "Nous devons signer le contrat avant la fin du mois.",
        "La température extérieure est de moins trois degrés.",
        "Tournez à gauche au prochain feu et continuez pendant deux kilomètres.",
        "La facture de l'imprimante est de 245 euros.",
        "Pouvez-vous vérifier si le serveur a été redémarré la nuit dernière ?",
        "La date limite du projet est le douze mars.",
        "Cette fonctionnalité devrait fonctionner hors ligne, sans connexion réseau.",
        "Elle est rentrée tard et personne ne l'a entendue.",
        "Ouvrez la fenêtre des paramètres et activez la traduction.",
        "La vieille batterie dure moins de deux heures.",
        "Tout le monde au bureau préfère une réunion plus courte.",
        "Si le microphone est coupé, aucun audio n'est enregistré.",
        "Le colis a été livré le trois juin.",
        "J'ai oublié mes lunettes, donc le texte est flou.",
        "Il a dit que la mise à jour dure environ quinze minutes.",
    ),
    "es": (
        "La reunión empieza a las nueve de la mañana.",
        "Envía el informe a Anna antes del viernes, por favor.",
        "Te llamo en diez minutos.",
        "El tren a Berlín sale de la vía cuatro.",
        "Mi número de teléfono es 0176 5532 1188.",
        "Tenemos que firmar el contrato antes de fin de mes.",
        "La temperatura exterior es de menos tres grados.",
        "Gira a la izquierda en el siguiente semáforo y sigue dos kilómetros.",
        "La factura de la impresora es de 245 euros.",
        "¿Puedes comprobar si el servidor se reinició la noche pasada?",
        "El plazo del proyecto es el doce de marzo.",
        "Esta función debería funcionar sin conexión, sin red.",
        "Llegó a casa tarde y nadie la oyó.",
        "Abre la ventana de ajustes y activa la traducción.",
        "La batería vieja dura menos de dos horas.",
        "Todos en la oficina prefieren una reunión más corta.",
        "Si el micrófono está silenciado, no se graba audio.",
        "El paquete se entregó el tres de junio.",
        "Olvidé mis gafas, así que el texto está borroso.",
        "Dijo que la actualización dura unos quince minutos.",
    ),
    "it": (
        "La riunione inizia alle nove di mattina.",
        "Invia il rapporto ad Anna entro venerdì, per favore.",
        "Ti richiamo tra dieci minuti.",
        "Il treno per Berlino parte dal binario quattro.",
        "Il mio numero di telefono è 0176 5532 1188.",
        "Dobbiamo firmare il contratto entro la fine del mese.",
        "La temperatura esterna è di tre gradi sotto zero.",
        "Gira a sinistra al prossimo semaforo e continua per due chilometri.",
        "La fattura della stampante è di 245 euro.",
        "Puoi verificare se il server è stato riavviato la notte scorsa?",
        "La scadenza del progetto è il dodici marzo.",
        "Questa funzione dovrebbe funzionare offline, senza connessione di rete.",
        "Lei è tornata a casa tardi e nessuno l'ha sentita.",
        "Apri la finestra delle impostazioni e attiva la traduzione.",
        "La vecchia batteria dura meno di due ore.",
        "Tutti in ufficio preferiscono una riunione più corta.",
        "Se il microfono è silenziato, non viene registrato alcun audio.",
        "Il pacco è stato consegnato il tre giugno.",
        "Ho dimenticato gli occhiali, quindi il testo è sfocato.",
        "Ha detto che l'aggiornamento dura circa quindici minuti.",
    ),
    "nl": (
        "De vergadering begint om negen uur 's ochtends.",
        "Stuur het rapport alsjeblieft vrijdag naar Anna.",
        "Ik bel je over tien minuten terug.",
        "De trein naar Berlijn vertrekt van spoor vier.",
        "Mijn telefoonnummer is 0176 5532 1188.",
        "We moeten het contract voor het einde van de maand ondertekenen.",
        "De buitentemperatuur is min drie graden.",
        "Sla links af bij het volgende verkeerslicht en rij twee kilometer door.",
        "De factuur voor de printer is 245 euro.",
        "Kun je controleren of de server vannacht is herstart?",
        "De deadline van het project is twaalf maart.",
        "Deze functie moet offline werken, zonder netwerkverbinding.",
        "Ze kwam laat thuis en niemand hoorde haar.",
        "Open het instellingenvenster en schakel vertalen in.",
        "De oude batterij houdt het minder dan twee uur vol.",
        "Iedereen op kantoor prefereert een kortere vergadering.",
        "Als de microfoon is gedempt, wordt er geen audio opgenomen.",
        "Het pakket werd op drie juni bezorgd.",
        "Ik vergat mijn bril, dus de tekst is wazig.",
        "Hij zei dat de update ongeveer vijftien minuten duurt.",
    ),
    "uk": (
        "Зустріч починається о дев'ятій ранку.",
        "Будь ласка, надішли звіт Анні до п'ятниці.",
        "Я передзвоню тобі через десять хвилин.",
        "Поїзд до Берлина відправляється з четвертої платформи.",
        "Мій номер телефону 0176 5532 1188.",
        "Ми повинні підписати договір до кінця місяця.",
        "Температура зовні мінус три градуси.",
        "Поверни ліворуч на наступному світлофорі і продовж два кілометри.",
        "Рахунок за принтер 245 євро.",
        "Можеш перевірити, чи сервер перезапустили минулої ночі?",
        "Термін проєкту дванадцяте березня.",
        "Ця функція має працювати офлайн, без мережевого з'єднання.",
        "Вона прийшла додому пізно, і ніхто її не почув.",
        "Відкри вікно налаштувань і увімкни переклад.",
        "Старий акумулятор тримає не більше двох годин.",
        "Всім в офісі подобається коротша зустріч.",
        "Якщо мікрофон вимкнено, аудіо не записується.",
        "Посилку доставили третього червня.",
        "Я забув окуляри, тому текст нечіткий.",
        "Він сказав, що оновлення триває близько п'ятнадцяти хвилин.",
    ),
    "cs": (
        "Schůzka začíná v devět hodin ráno.",
        "Prosím, poslij zprávu Anně do pátku.",
        "Zavolám ti zpátky za deset minut.",
        "Vlak do Berlína odjíždí ze čtvrté koleje.",
        "Moje telefonní číslo je 0176 5532 1188.",
        "Musíme podepsat smlouvu do konce měsíce.",
        "Venkovní teplota je minus tři stupně.",
        "Odboč doleva na dalším semaforu a pokračuj dva kilometry.",
        "Faktura za tiskárnu je 245 euro.",
        "Můžeš zkontrolovat, zda byl server v noci restartován?",
        "Lhůta projektu je dvanáctého března.",
        "Tato funkce by měla fungovat offline, bez síťového připojení.",
        "Přišla domů pozdě a nikdo ji neslyšel.",
        "Otevři okno nastavení a zapni překlad.",
        "Stará baterie vydrží méně než dvě hodiny.",
        "Všem v kanceláři vyhovuje kratší schůzka.",
        "Pokud je mikrofon ztlumen, nenahrává se žádný audio.",
        "Balík byl doručen třetího června.",
        "Zapomněl jsem brýle, takže text je rozmazaný.",
        "Řekl, že aktualizace trvá asi patnáct minut.",
    ),
    "sv": (
        "Mötet börjar klockan nio på morgonen.",
        "Skicka rapporten till Anna innan fredag, tack.",
        "Jag ringer upp dig om tio minuter.",
        "Tåget till Berlin avgår från spår fyra.",
        "Mitt telefonnummer är 0176 5532 1188.",
        "Vi måste skriva under avtalet innan månadsslutet.",
        "Temperaturen utomhus är minus tre grader.",
        "Sväng vänster vid nästa trafikljus och fortsätt två kilometer.",
        "Fakturan för skrivaren är 245 euro.",
        "Kan du kontrollera om servern startades om i natt?",
        "Projektets deadline är tolfte mars.",
        "Denna funktion ska fungera offline, utan nätverksanslutning.",
        "Hon kom hem sent och ingen hörde henne.",
        "Öppna inställningsfönstret och aktivera översättning.",
        "Det gamla batteriet håller i mindre än två timmar.",
        "Alla på kontoret föredrar ett kortare möte.",
        "Om mikrofonen är avstängd spelas ingen ljud upp.",
        "Paketet levererades den tredje juni.",
        "Jag glömde mina glasögon, så texten är suddig.",
        "Han sa att uppdateringen tar ungefär femton minuter.",
    ),
    "zh": (
        "会议在早上九点开始。",
        "请在周五之前把报告发给安娜。",
        "我十分钟后给你回电话。",
        "开往柏林的火车从四号站台出发。",
        "我的电话号码是0176 5532 1188。",
        "我们必须在月底之前签署合同。",
        "室外温度是零下三度。",
        "在下一个红绿灯左转，继续行驶两公里。",
        "打印机的发票是245欧元。",
        "你能查一下服务器昨晚是否重启过吗？",
        "项目的截止日期是三月十二日。",
        "这个功能应该离线运行，无需网络连接。",
        "她很晚才到家，没人听见她。",
        "打开设置窗口并启用翻译。",
        "旧电池续航不超过两个小时。",
        "办公室里的每个人都喜欢更短的会议。",
        "如果麦克风静音，就不会录制音频。",
        "包裹在六月三日送达。",
        "我忘了带眼镜，所以文字模糊。",
        "他说更新大约需要十五分钟。",
    ),
}


def tokens(text: str) -> list[str]:
    """Comparable units: digit groups, words in any script, single CJK characters."""
    text = text.lower()
    words = [
        word
        for word in re.findall(r"[^\W_]+", text)
        if len(word) > 2 and not all("\u4e00" <= char <= "\u9fff" for char in word)
    ]
    digits = re.findall(r"\d+", text)
    cjk = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    return digits + words + cjk


def coverage(reference: str, output: str) -> float:
    """Fraction of the reference units the translation kept."""
    needed = Counter(tokens(reference))
    if not needed:
        return 1.0
    return sum((needed & Counter(tokens(output))).values()) / sum(needed.values())


def peak_memory_mb() -> int:
    try:
        import psutil
    except ImportError:
        return 0
    return int(psutil.Process().memory_info().rss // (1024 * 1024))


def run_target(target: str, repeat: int, warmup: int) -> dict:
    info = translation.target_info(target)
    # Measure a cold load: the cache is empty, so the pack is read from disk.
    translation.forget_translator(target)
    before_mb = peak_memory_mb()
    started = time.perf_counter()
    translator = translation.get_translator(target)
    load_seconds = time.perf_counter() - started
    loaded_mb = peak_memory_mb()

    for _ in range(max(0, warmup)):
        for phrase in PHRASES:
            translator.translate(phrase)

    outputs: list[str] = []
    runs: list[dict[str, float]] = []
    peak_mb = loaded_mb
    for index in range(max(1, repeat)):
        started = time.perf_counter()
        pass_outputs = [translator.translate(phrase) for phrase in PHRASES]
        elapsed = time.perf_counter() - started
        runs.append(
            {
                "elapsed_s": round(elapsed, 4),
                "per_phrase_s": round(elapsed / len(PHRASES), 4),
            }
        )
        peak_mb = max(peak_mb, peak_memory_mb())
        if index == 0:
            outputs = pass_outputs

    translation.forget_translator(target)
    words = sum(len(phrase.split()) for phrase in PHRASES)
    result = {
        "target": target,
        "source_repo": info.source_repo,
        "license": info.license,
        "pack_mb": info.size_mb,
        "load_s": round(load_seconds, 4),
        "memory_mb": max(0, loaded_mb - before_mb),
        "peak_rss_mb": peak_mb,
        "elapsed_mean_s": round(statistics.fmean([r["elapsed_s"] for r in runs]), 4),
        "per_phrase_mean_s": round(
            statistics.fmean([r["per_phrase_s"] for r in runs]), 4
        ),
        "per_word_ms": round(
            statistics.fmean([r["elapsed_s"] for r in runs]) * 1000 / words, 2
        ),
        "runs": runs,
    }

    references = REFERENCES.get(target)
    if references is not None:
        scores = [
            coverage(reference, text)
            for reference, text in zip(references, outputs)
        ]
        with_digits = [reference for reference in references if re.search(r"\d", reference)]
        result.update(
            {
                "coverage_mean": round(statistics.fmean(scores), 3),
                "coverage_min": round(min(scores), 3),
                "digits_kept": sum(
                    re.findall(r"\d+", reference) == re.findall(r"\d+", outputs[index])
                    for index, reference in enumerate(references)
                    if re.search(r"\d", reference)
                ),
                "digit_cases": len(with_digits),
                "empty_outputs": sum(1 for text in outputs if not text.strip()),
                "outputs": outputs,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark VoiceType translation packs")
    parser.add_argument(
        "--targets",
        default=",".join(translation.PACKS),
        help="comma-separated target codes (default: every pack)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="measured passes (default 1)")
    parser.add_argument("--warmup", type=int, default=1, help="warmup passes (default 1)")
    parser.add_argument("--output", default="", help="write JSON results to this path")
    args = parser.parse_args()

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    for target in targets:
        if target not in translation.PACKS:
            raise SystemExit(f"{target} has no translation pack")
        if not translation.pack_available(target):
            raise SystemExit(
                f"The {target} pack is not installed. Download it in Settings, "
                "or build it with scripts/build_mt_packs.py."
            )

    words = sum(len(phrase.split()) for phrase in PHRASES)
    print(
        f"VoiceType {__version__} translation benchmark\n"
        f"  {len(PHRASES)} phrases, {words} words\n"
        f"  targets: {', '.join(targets)}\n"
        f"  cpu threads: {min(4, os.cpu_count() or 1)}"
    )

    results = []
    for target in targets:
        result = run_target(target, args.repeat, args.warmup)
        results.append(result)
        line = (
            f"  {target}: load {result['load_s']:.2f} s, "
            f"{result['per_phrase_mean_s']:.2f} s/phrase, "
            f"{result['per_word_ms']:.1f} ms/word, "
            f"pack memory {result['memory_mb']} MB, peak RSS {result['peak_rss_mb']} MB"
        )
        if "coverage_mean" in result:
            line += (
                f", coverage {result['coverage_mean']:.2f} "
                f"(min {result['coverage_min']:.2f}, digits "
                f"{result['digits_kept']}/{result['digit_cases']}, "
                f"empty {result['empty_outputs']})"
            )
        print(line)

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "voicetype_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "phrases": len(PHRASES),
        "words": words,
        "repeat": max(1, args.repeat),
        "cpu_threads": min(4, os.cpu_count() or 1),
        "results": results,
    }

    print(f"\nSummary: {len(results)} packs, {max(1, args.repeat)} pass(es)")

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Saved {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
