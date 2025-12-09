# midi_to_sidecar.py
# Convierte un MIDI (8 compases, 4/4) a un sidecar YAML con: key, mode, bars[8]
# Requiere: mido, pyyaml  ->  pip install mido PyYAML

import sys
from pathlib import Path
from typing import List, Tuple, Dict
import math
import yaml
from mido import MidiFile, MetaMessage

# -------------------- Utiles de teoría --------------------
NOTE_NAMES_SHARP = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
NAME_TO_PC = {n:i for i,n in enumerate(NOTE_NAMES_SHARP)}

MAJOR_PCS     = [0,2,4,5,7,9,11]
NATMIN_PCS    = [0,2,3,5,7,8,10]
HARMONIC_PCS  = [0,2,3,5,7,8,11]

# Triadas diatónicas por modo (tipo: 'M', 'm', 'dim')
TRIADS_MAJOR = ['M','m','m','M','M','m','dim']
TRIADS_NMIN  = ['m','dim','M','m','m','M','M']
TRIADS_HMIN  = ['m','dim','M','m','M','M','dim']  # pragmático para pop

# Perfiles Krumhansl (mayor / menor) normalizados aprox.
# Fuente típica (K-S): mayor=[6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88]
#                      menor=[6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17]
KS_MAJOR = [6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88]
KS_MINOR = [6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17]

def rotate(lst, k):
    k %= len(lst)
    return lst[-k:] + lst[:-k]

def dot(a,b):
    return sum(x*y for x,y in zip(a,b))

# -------------------- Lectura de notas --------------------
def track_to_abs_events(track) -> List[Tuple[int, object]]:
    """Convierte delta times a tiempos absolutos por track."""
    t = 0
    abs_events = []
    for msg in track:
        t += msg.time
        abs_events.append((t, msg))
    return abs_events

def collect_note_intervals(mid: MidiFile) -> List[Tuple[int,int,int,int,int]]:
    """
    Devuelve lista de notas: (start_tick, end_tick, pitch, channel, velocity)
    Ignora canal 9 (batería GM).
    """
    notes = []
    for tr in mid.tracks:
        on_stack: Dict[Tuple[int,int], Tuple[int,int]] = {}  # (ch,note) -> (start_tick, velocity)
        abs_events = track_to_abs_events(tr)
        for t, msg in abs_events:
            if msg.is_meta:
                continue
            if not hasattr(msg, 'channel'):
                continue
            ch = msg.channel
            if ch == 9:  # canal 10 -> drums
                continue
            if msg.type == 'note_on' and msg.velocity > 0:
                on_stack[(ch, msg.note)] = (t, msg.velocity)
            elif (msg.type == 'note_off') or (msg.type == 'note_on' and msg.velocity == 0):
                key = (ch, msg.note)
                if key in on_stack:
                    start, vel = on_stack.pop(key)
                    if t > start:
                        notes.append((start, t, msg.note, ch, vel))
    return notes

def read_first_timesig(mid: MidiFile) -> Tuple[int,int]:
    """Lee primer time_signature (numerador, denominador). Si falta, 4/4."""
    for tr in mid.tracks:
        for msg in tr:
            if isinstance(msg, MetaMessage) and msg.type == 'time_signature':
                return msg.numerator, msg.denominator
    return 4,4

# -------------------- Histos y segmentación --------------------
def weighted_pc_hist(notes, start_tick, end_tick) -> List[float]:
    """
    Histograma de pitch-class (12) ponderado por duración de solapamiento con [start,end).
    """
    L = [0.0]*12
    for s,e,p,ch,vel in notes:
        if e <= start_tick or s >= end_tick:
            continue
        a = max(s, start_tick)
        b = min(e, end_tick)
        w = max(0, b - a)
        if w > 0:
            L[p % 12] += w
    return L

def choose_key_mode(pc_hist: List[float]) -> Tuple[str, str]:
    """
    Estima (key_name, mode_str) usando Krumhansl y decide menor_natural vs menor_armónico
    según presencia de la sensible.
    """
    # Elegir mayor/minor y tónica (rotación) por correlación
    best = None
    best_mode = None
    best_key_pc = None
    for mode_name, prof in [('major', KS_MAJOR), ('minor', KS_MINOR)]:
        for k in range(12):
            score = dot(pc_hist, rotate(prof, k))
            if best is None or score > best:
                best = score
                best_mode = mode_name
                best_key_pc = k
    key_name = NOTE_NAMES_SHARP[best_key_pc]

    # Si menor: natural vs armónico
    if best_mode == 'minor':
        # sensible = 11 semitonos sobre tónica (pc = key_pc+11)
        sens_pc = (best_key_pc + 11) % 12
        nat7_pc = (best_key_pc + 10) % 12
        total = sum(pc_hist) + 1e-9
        sens_w = pc_hist[sens_pc] / total
        nat7_w = pc_hist[nat7_pc] / total
        # Umbral simple: si la sensible “pesa” bastante más que la b7 natural → armónico
        mode_str = 'minor_harmonic' if sens_w > (nat7_w + 0.02) and sens_w > 0.04 else 'minor_natural'
    else:
        mode_str = 'major'
    return key_name, mode_str

def scale_pcs_for_mode(key_pc: int, mode: str) -> List[int]:
    base = {'major':MAJOR_PCS, 'minor_natural':NATMIN_PCS, 'minor_harmonic':HARMONIC_PCS}[mode]
    return [ (key_pc + x) % 12 for x in base ]

def triad_for_degree(key_pc: int, mode: str, degree_idx: int) -> List[int]:
    """Devuelve PCs de la triada diatónica del grado (0..6) en el modo."""
    scale = scale_pcs_for_mode(key_pc, mode)
    root = scale[degree_idx]
    if mode == 'major':
        q = TRIADS_MAJOR[degree_idx]
    elif mode == 'minor_harmonic':
        q = TRIADS_HMIN[degree_idx]
    else:
        q = TRIADS_NMIN[degree_idx]
    if q == 'M':   return [root, (root+4)%12, (root+7)%12]
    if q == 'm':   return [root, (root+3)%12, (root+7)%12]
    if q == 'dim': return [root, (root+3)%12, (root+6)%12]
    return [root, (root+4)%12, (root+7)%12]

def roman_for_degree(mode: str, degree_idx: int) -> str:
    """Romano sin tensiones, con ° si es disminuido."""
    romans = ['I','II','III','IV','V','VI','VII']
    if mode == 'major':
        q = TRIADS_MAJOR[degree_idx]
    elif mode == 'minor_harmonic':
        q = TRIADS_HMIN[degree_idx]
    else:
        q = TRIADS_NMIN[degree_idx]
    r = romans[degree_idx]
    if q == 'm':
        return r.lower()
    if q == 'dim':
        return r.lower() + '°'
    return r

def best_roman_for_segment(notes, key_pc: int, mode: str, start_tick: int, end_tick: int) -> Tuple[str, float]:
    """
    Elige el grado (romano) cuya triada diatónica mejor explica el histograma del segmento.
    Retorna (romano, cobertura) con cobertura = peso_en_triada / peso_total (0..1).
    """
    hist = weighted_pc_hist(notes, start_tick, end_tick)
    total = sum(hist)
    if total <= 0:
        return 'I', 0.0
    best_score = -1.0
    best_deg = 0
    for d in range(7):
        triad = set(triad_for_degree(key_pc, mode, d))
        score = sum(hist[pc] for pc in triad)
        if score > best_score:
            best_score = score
            best_deg = d
    roman = roman_for_degree(mode, best_deg)
    coverage = best_score / total
    return roman, coverage

# -------------------- Conversión principal --------------------
def midi_to_yaml_sidecar(mid_path: Path) -> Dict:
    mid = MidiFile(mid_path)
    ppq = mid.ticks_per_beat
    num, den = read_first_timesig(mid)
    if num != 4 or den != 4:
        print(f"[WARN] Time Signature {num}/{den} detectado (se esperaba 4/4). Se continuará igualmente.")
    bar_ticks = int(ppq * 4 * (num/den))  # para 4/4, es 4*ppq

    notes = collect_note_intervals(mid)

    # Longitud total y verificación mínima de 8 compases
    if not notes:
        raise ValueError("No se encontraron notas (¿MIDI vacío o sólo percusión?).")
    end_max = max(e for _,e,_,_,_ in notes)
    min_needed = bar_ticks * 8
    if end_max < min_needed:
        print(f"[WARN] El MIDI parece tener menos de 8 compases ({end_max} ticks < {min_needed}). Se analizarán los primeros 8 compases igualmente, pudiendo haber silencio al final.")

    # Histograma global y estimación de tonalidad/modo
    global_hist = weighted_pc_hist(notes, 0, min_needed)
    key_name, mode = choose_key_mode(global_hist)
    key_pc = NAME_TO_PC[key_name]
    print(f"[INFO] Estimado: key={key_name} mode={mode}")

    bars = []
    for bar_idx in range(8):
        s = bar_ticks * bar_idx
        e = s + bar_ticks

        # Mitades del compás
        midpt = s + bar_ticks//2
        r1, c1 = best_roman_for_segment(notes, key_pc, mode, s, midpt)
        r2, c2 = best_roman_for_segment(notes, key_pc, mode, midpt, e)

        if r1 == r2:
            bars.append(r1)
        else:
            bars.append(f"{r1}|{r2}")

    data = {
        'key': key_name,
        'mode': mode,
        'bars': bars
    }
    return data

def main():
    if len(sys.argv) < 2:
        print("Uso: python midi_to_sidecar.py <archivo.mid>")
        sys.exit(1)
    mid_path = Path(sys.argv[1])
    if not mid_path.exists():
        print(f"ERROR: No existe {mid_path}")
        sys.exit(1)

    sidecar = midi_to_yaml_sidecar(mid_path)
    yml_path = mid_path.with_suffix('.yml')
    with open(yml_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(sidecar, f, sort_keys=False, allow_unicode=True)
    print(f"[OK] Generado: {yml_path}")

if __name__ == "__main__":
    main()
