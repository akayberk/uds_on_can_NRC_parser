from uds_codes import UDS_SERVICES, NRC_MAP, DID_MAP
from collections import defaultdict
import re



# ----------------------------
# Translations (EN/TR/DE)
# ----------------------------



TRANSLATIONS = {
    "EN": {
        "title": "UDS-on-CAN ASC Parser",
        "open_btn": "Open .asc",
        "export_btn": "Export CSV",
        "no_file": "No file loaded",
        "file_loaded": "File loaded: {path}\nLines: {lines}\n\n",
        "parsing_complete": "Parsing complete.",
        "tx_count": "TX count: {tx}  RX count: {rx}",
        "paired_summary": "Paired: {paired}  Unpaired TX: {utx}  Unpaired RX: {urx}",
        "negative_label": "NEGATIVE",
        "positive_label": "POSITIVE",
    },
    "TR": {
        "title": "UDS-on-CAN ASC Ayrıştırıcı",
        "open_btn": ".asc Aç",
        "export_btn": "CSV Dışa Aktar",
        "no_file": "Dosya yüklenmedi",
        "file_loaded": "Yüklendi: {path}\nSatır: {lines}\n\n",
        "parsing_complete": "Ayrıştırma tamamlandı.",
        "tx_count": "TX sayısı: {tx}  RX sayısı: {rx}",
        "paired_summary": "Eşleşen: {paired}  Eşleşmemiş TX: {utx}  Eşleşmemiş RX: {urx}",
        "negative_label": "NEGATİF",
        "positive_label": "POZİTİF",
    },
    "DE": {
        "title": "UDS-on-CAN ASC Parser",
        "open_btn": ".asc Öffnen",
        "export_btn": "CSV Export",
        "no_file": "Keine Datei geladen",
        "file_loaded": "Datei geladen: {path}\nZeilen: {lines}\n\n",
        "parsing_complete": "Parsen abgeschlossen.",
        "tx_count": "TX Anzahl: {tx}  RX Anzahl: {rx}",
        "paired_summary": "Gepaart: {paired}  Ungepaarte TX: {utx}  Ungepaarte RX: {urx}",
        "negative_label": "NEGATIV",
        "positive_label": "POSITIV",
    },
}


# ----------------------------
# ASC line regex
# ----------------------------

LINE_RE = re.compile(
    r"^\s*(?P<time>\d+\.\d+)\s+"
    r"(?P<channel>\d+)\s+"
    r"(?P<canid>[0-9A-Fa-f]+x)\s+"
    r"(?P<dir>Rx|Tx)\s+"
    r"d\s+(?P<dlc>\d+)\s+"
    r"(?P<data>(?:[0-9A-Fa-f]{2}\s+){0,16})",
    re.MULTILINE,
)

# ----------------------------
# Parser class (with ISO-TP reassembly and special rules)
# ----------------------------

class AscParser:
    def __init__(self, language="EN"):
        self.language = language
        self.tx_entries = []
        self.rx_entries = []
        self.other_entries = []
        self.paired = []
        self.unpaired_tx = []
        self.unpaired_rx = []
        # reassembly buffers keyed by numeric CAN ID
        self.reassembly_buffers = defaultdict(lambda: {"data": b"", "expected_len": 0})

    def parse_line(self, line):
        m = LINE_RE.match(line)
        if not m:
            return None
        ts = float(m.group("time"))
        ch = int(m.group("channel"))
        canid_raw = m.group("canid")
        canid = canid_raw.rstrip("xX").upper()
        direction = m.group("dir")
        dlc = int(m.group("dlc"))
        data_field = m.group("data") or ""
        data_bytes = hex_bytes_to_ints(data_field)

        # Normalize: numeric can id key for reassembly
        try:
            canid_num = int(canid, 16)
        except Exception:
            canid_num = None

        # ISO-TP reassembly for Rx frames: handle SF/FF/CF/FC
        # We'll attempt to produce a "normalized" uds_payload (list[int]) that has PCI stripped.
        final_payload = None
        # Only attempt reassembly for Rx frames (we don't reassemble Tx from tester)
        if direction == "Rx" and data_bytes:
            info = iso_tp_frame_info(data_bytes)
            ft = info["frame_type"]
            if ft == 0:  # Single Frame: data_bytes already UDS payload
                final_payload = info["data_bytes"]
            elif ft == 1 and canid_num is not None:  # First Frame: store initial bytes and expected length
                total = info.get("ff_total_len", 0)
                part = bytes(info.get("data_bytes", []))
                self.reassembly_buffers[canid_num]["data"] = part
                self.reassembly_buffers[canid_num]["expected_len"] = total
                # don't return a parsed entry yet — wait for CFs
                return None
            elif ft == 2 and canid_num is not None:  # Consecutive Frame
                buf = self.reassembly_buffers.get(canid_num)
                if not buf or buf["expected_len"] == 0:
                    # No matching FF: maybe this is a stray CF; ignore
                    return None
                # append CF payload
                buf["data"] += bytes(info.get("data_bytes", []))
                if len(buf["data"]) >= buf["expected_len"]:
                    # finalize
                    full = buf["data"][:buf["expected_len"]]
                    final_payload = list(full)
                    try:
                        del self.reassembly_buffers[canid_num]
                    except Exception:
                        pass
                else:
                    # incomplete, wait for more CFs
                    return None
            elif ft == 3:
                # Flow Control frame (controller->sender). We ignore FC frames as data; they are control.
                # But we want to surface them if debugging; for now ignore (do not create parsed entry).
                return None
            else:
                # No PCI or unknown structure — treat data_bytes as raw UDS payload
                final_payload = data_bytes
        else:
            # For Tx frames or empty payloads: keep original data_bytes (no PCI strip)
            final_payload = data_bytes

        # final_payload is list[int] now (maybe empty)
        if final_payload is None:
            final_payload = []

        # Detect "suppress positive" flag: if Tx payload contains 0x3E and next byte has 0x80 set (3E 80)
        suppress_positive = False
        if direction == "Tx" and len(final_payload) >= 2:
            # Look for exact pattern anywhere in Tx first two bytes sequence
            for i in range(len(final_payload) - 1):
                if final_payload[i] == 0x3E and (final_payload[i + 1] & 0x80):
                    suppress_positive = True
                    break

        parsed = {
            "timestamp": ts,
            "channel": ch,
            "can_id": canid,  # string uppercase hex
            "can_id_num": canid_num,
            "dir": direction,
            "dlc": dlc,
            "data": final_payload,
            "raw_line": line.rstrip("\n"),
            "process_udsoncan": canid.upper().startswith("18DA"),
            "suppress_positive": suppress_positive,
        }
        return parsed

    def ingest(self, asc_text):
        self.tx_entries.clear()
        self.rx_entries.clear()
        self.other_entries.clear()
        for line in asc_text.splitlines():
            parsed = self.parse_line(line)
            if parsed is None:
                continue
            process_flag = parsed.get("process_udsoncan", parsed.get("can_id", "").upper().startswith("18DA"))
            if not process_flag:
                self.other_entries.append(parsed)
                continue

            # TesterPresent (0x3E) filter — robust for PCI-present and PCI-stripped formats
            d = parsed.get("data", [])
            if len(d) >= 2 and d[1] == 0x3E:
                continue
            if len(d) >= 1 and d[0] == 0x3E:
                continue

            if parsed["dir"] == "Tx":
                self.tx_entries.append(parsed)
            else:
                self.rx_entries.append(parsed)

    # _identify_sid updated to be robust for PCI present vs stripped payloads
    def _identify_sid(self, data):
        if not data:
            return None
        # If PCI present (e.g., SF with length as first byte), handle separately:
        pci_info = iso_tp_frame_info(data)
        uds = pci_info.get("data_bytes", data)
        if not uds:
            return None
        # Negative responses: could appear either as [0x7F, ReqSID, NRC] or with PCI -> [PCI, 0x7F, ReqSID, NRC]
        if len(uds) >= 2 and uds[1] == 0x7F:
            return uds[3]  # original req SID
        # Positive responses echo SID+0x40
        first = uds[0]
        if first >= 0x40 and (first - 0x40) in UDS_SERVICES:
            return first - 0x40
        # For requests, first byte is SID
        if first in UDS_SERVICES:
            return first
        # fallback
        return first

    def pair_tx_rx(self, max_time_delta=2.0):
        """
        Enhanced pairing:
         - For each Tx, collect candidate Rx frames that are after Tx and within time window.
         - Respect suppress_positive: if tx has suppress_positive True, ignore positive Rx results (only negative shown).
         - Handle multiple 0x78 ResponsePending: record first and last 0x78 occurrences; show only first+last.
         - If multiple Rx match, pick the final decisive response (non-0x78 positive/negative). If none, pick last 0x78.
         - Ensure order: scanning rx entries in chronological order.
        """
        self.paired.clear()
        self.unpaired_tx.clear()
        rx_pool = list(self.rx_entries)  # shallow copy
        # index to start scanning from to speed up
        rx_start_index = 0

        for tx in self.tx_entries:
            candidates = []
            # gather rx candidates
            for idx in range(rx_start_index, len(rx_pool)):
                rx = rx_pool[idx]
                if rx is None:
                    continue
                if rx["timestamp"] < tx["timestamp"]:
                    continue
                if (rx["timestamp"] - tx["timestamp"]) > max_time_delta:
                    continue
                # check SID/DID matching heuristics
                tx_id = self._identify_sid(tx["data"])
                rx_id = self._identify_sid(rx["data"])
                # extract DIDs where applicable (22/2E)
                tx_did = None
                rx_did = None
                if tx_id in (0x22, 0x2E) and len(tx["data"]) >= 3:
                    tx_did = (tx["data"][1] << 8) | tx["data"][2]
                # rx: if positive response (SID+0x40) has DID next
                # attempt to parse UDS payload (pci stripped) using iso_tp_frame_info
                rx_payload = iso_tp_frame_info(rx["data"]).get("data_bytes", rx["data"])
                if rx_payload:
                    # if positive response 0x40+sid and length >=3, may include DID at [1:3]
                    if rx_payload[0] >= 0x40:
                        maybe_req_sid = rx_payload[0] - 0x40
                        if maybe_req_sid in (0x22, 0x2E) and len(rx_payload) >= 3:
                            rx_did = (rx_payload[1] << 8) | rx_payload[2]
                    # if negative with PCI present it may be at idx 1
                    if rx_payload[0] == 0x7F and len(rx_payload) >= 3:
                        rx_req_sid = rx_payload[1]
                        # negative does not carry DID usually
                # Now decide if rx is candidate: same SID or negative for that SID
                match_sid = (tx_id is not None and rx_id is not None and tx_id == rx_id)
                is_negative = False
                # robust negative detection for rx_payload
                if len(rx_payload) >= 2 and rx_payload[0] == 0x7F:
                    is_negative = True
                elif len(rx_payload) >= 2 and rx_payload[1] == 0x7F:
                    # PCI present case
                    is_negative = True
                # consider candidate if SID matches or negative or DID matches when applicable
                did_ok = (tx_did is None) or (rx_did is None) or (tx_did == rx_did)
                if (match_sid and did_ok) or is_negative:
                    candidates.append((idx, rx))

            if not candidates:
                # no rx found within window
                self.unpaired_tx.append(tx)
                continue

            # From candidates, prefer:
            # 1) last non-0x78 (decisive positive or negative)
            # 2) if none, pick last 0x78, but also record first 0x78 and last 0x78
            first_78 = None
            last_78 = None
            final_rx_idx = None
            final_rx = None
            for idx, rx in candidates:
                # extract NRC if negative
                rp = iso_tp_frame_info(rx["data"]).get("data_bytes", rx["data"])
                nrc = None
                is_neg = False
                if rp:
                    if rp[0] == 0x7F and len(rp) >= 3:
                        is_neg = True
                        nrc = rp[2]
                    elif len(rp) >= 2 and rp[1] == 0x7F and len(rp) >= 4:
                        is_neg = True
                        nrc = rp[3] if len(rp) > 3 else None
                # track 0x78
                if is_neg and nrc == 0x78:
                    if first_78 is None:
                        first_78 = (idx, rx)
                    last_78 = (idx, rx)
                    # continue looking for final
                    continue
                # if this is a decisive response (not 0x78), choose it as final
                final_rx_idx = idx
                final_rx = rx

            if final_rx is None:
                # no decisive response, but we have at least one 0x78
                if last_78 is not None:
                    final_rx_idx, final_rx = last_78
                    # we'll keep track of first_78 too
            # If still none (shouldn't happen), pick last candidate
            if final_rx is None:
                final_rx_idx, final_rx = candidates[-1]

            # Now apply suppress_positive: if tx requests suppress and final_rx is positive, skip it
            if tx.get("suppress_positive") and final_rx is not None:
                # determine if final_rx is positive
                rp = iso_tp_frame_info(final_rx["data"]).get("data_bytes", final_rx["data"])
                is_pos = False
                if rp and rp[0] >= 0x40:
                    is_pos = True
                # if positive and we must suppress -> treat as no rx (i.e., leave tx unpaired)
                if is_pos:
                    self.unpaired_tx.append(tx)
                    continue

            # consume final_rx from rx_pool (mark None)
            rx_pool[final_rx_idx] = None
            # also optionally mark any intermediate 0x78s as consumed
            if first_78 is not None and last_78 is not None and first_78[0] != final_rx_idx:
                # remove any 0x78 entries between first_78 and last_78
                fi = first_78[0]
                li = last_78[0]
                for k in range(fi, li + 1):
                    if 0 <= k < len(rx_pool):
                        rx_pool[k] = None
            # append pair and store optional 0x78 metadata on pair
            pair_meta = {"tx": tx, "rx": final_rx, "first_78": first_78[1] if first_78 else None, "last_78": last_78[1] if last_78 else None}
            self.paired.append(pair_meta)

        # rebuild unpaired_rx list from rx_pool leftovers
        self.unpaired_rx = [x for x in rx_pool if x is not None]

    def interpret_pair(self, pair_meta):
        """
        Accepts the pair_meta dict produced in pair_tx_rx and returns human-friendly dict.
        Handles PCI-present vs stripped, ASCII conversion for data payload sections (after DID).
        """
        tx = pair_meta["tx"]
        rx = pair_meta["rx"]
        first_78 = pair_meta.get("first_78")
        last_78 = pair_meta.get("last_78")

        # identify SIDs
        tx_sid = self._identify_sid(tx["data"])
        rx_sid = self._identify_sid(rx["data"]) if rx else None

        # hex strings
        tx_hex = ints_to_hex_str(tx["data"])
        rx_hex = ints_to_hex_str(rx["data"]) if rx else ""

        # robust negative detection and NRC extraction
        def extract_nrc_from_payload(payload):
            if not payload:
                return None
            # payload is UDS payload (PCI stripped where applicable)
            if payload[0] == 0x7F and len(payload) >= 3:
                return payload[2]
            # case where PCI present (unlikely here): check second byte
            if len(payload) >= 4 and payload[1] == 0x7F:
                return payload[3]
            return None

        rp = iso_tp_frame_info(rx["data"]).get("data_bytes", rx["data"]) if rx else []
        nrc = extract_nrc_from_payload(rp)
        is_negative = (nrc is not None)

        nrc_text = NRC_MAP.get(nrc, f"UnknownNRC(0x{nrc:02X})") if nrc is not None else ""

        # DID extraction for TX (22/2E)
        did = None
        did_name = ""
        if tx_sid in (0x22, 0x2E) and len(tx["data"]) >= 3:
            did = (tx["data"][1] << 8) | tx["data"][2]
            did_name = DID_MAP.get(did, f"DID_0x{did:04X}")

        # For positive responses, extract the data bytes after DID (if present) and convert to ASCII where reasonable
        ascii_text = ""
        is_positive = False
        if rp:
            # determine positive: first byte >= 0x40
            first_byte = rp[0]
            if first_byte >= 0x40:
                is_positive = True
                # if response to DID (22), DID bytes likely at rp[1:3], payload starts at rp[3:]
                payload_after_did = []
                if first_byte - 0x40 in (0x22, 0x2E) and len(rp) >= 3:
                    payload_after_did = rp[3:]
                else:
                    # otherwise drop first byte (SID) and take rest
                    payload_after_did = rp[1:]
                if payload_after_did:
                    ascii_text = bytes_to_ascii_str(payload_after_did)

        service_name = UDS_SERVICES.get(tx_sid, (f"SID_0x{tx_sid:02X}", ""))[0] if tx_sid else ""

        result = {
            "tx_time": tx["timestamp"],
            "rx_time": rx["timestamp"] if rx else None,
            "tx_can_id": tx["can_id"],
            "rx_can_id": rx["can_id"] if rx else "",
            "tx_payload": tx_hex,
            "rx_payload": rx_hex,
            "service_sid": tx_sid,
            "service_name": service_name,
            "did": did,
            "did_name": did_name,
            "is_negative": is_negative,
            "nrc": nrc,
            "nrc_text": nrc_text,
            "ascii": ascii_text,
            "raw_tx": tx["raw_line"],
            "raw_rx": rx["raw_line"] if rx else "",
            "time_delta": (rx["timestamp"] - tx["timestamp"]) if rx else None,
            "first_78": first_78["raw_line"] if first_78 else None,
            "last_78": last_78["raw_line"] if last_78 else None,
            "suppress_positive": tx.get("suppress_positive", False),
        }
        return result

    def interpret_all_pairs(self):
        return [self.interpret_pair(pm) for pm in self.paired]

def hex_bytes_to_ints(byte_str):
    out = []
    for tok in byte_str.strip().split():
        try:
            out.append(int(tok, 16))
        except Exception:
            continue
    return out


def ints_to_hex_str(lst):
    return " ".join(f"{b:02X}" for b in lst) if lst else ""


def bytes_to_ascii_str(bts):
    try:
        # Map printable ASCII; replace non-printable with '.'
        s = "".join(chr(b) if 32 <= b <= 126 else "." for b in bts)
        return s
    except Exception:
        return ""


# ISO-TP helpers
def iso_tp_frame_info(payload_bytes):
    """
    Given raw bytes list (list of ints) representing a CAN frame payload,
    determine PCI type and provide useful slices.
    Returns dict with:
      - frame_type: 0 SF,1 FF,2 CF,3 FC, None if empty
      - sf_len: length for SF (if SF)
      - ff_total_len: total length if FF
      - data_bytes: bytes of UDS payload (excluding PCI bytes) if determinable
      - raw_payload: original bytes list
    Note: Many .asc logs include PCI in the captured data; some pre-processors strip PCI.
    This function is conservative: it inspects first byte nibble.
    """
    if not payload_bytes:
        return {"frame_type": None, "data_bytes": [], "raw_payload": payload_bytes}

    b0 = payload_bytes[0]
    high_nibble = (b0 & 0xF0) >> 4 # returns b0 & 0xF0 with the bits shifted to to right by 4 places.
    #0xF0 in binary is 11110000.
    #This masks the high nibble of b0, effectively zeroing out the lower nibble.
    #Example:
    #If b0 = 0x30 (binary 00110000)
    #b0 & 0xF0 = 0011 0000 &(AND OPERATOR) 1111 0000 = 00110000 (high nibble preserved)
    # hex 30 = binary 0011 0000 ||||  0011 0000 turns into 0000 0011 (shifted 4 places to the right)
    # 0000 0011 is 3, 0000 0010 is 2, 0000 0001 is 1, 0000 0000 is 0.
    # Single Frame: 0x0n -> low nibble is length, attached immediately after b0
    if high_nibble == 0x0:
        sf_len = b0 & 0x0F
        data_bytes = payload_bytes[1:1 + sf_len]
        return {"frame_type": 0, "sf_len": sf_len, "data_bytes": data_bytes, "raw_payload": payload_bytes}
    # First Frame: 0x1n -> total len = (n<<8) + payload_bytes[1]
    if high_nibble == 0x1 and len(payload_bytes) >= 2:
        total_len = ((b0 & 0x0F) << 8) + payload_bytes[1]
        data_bytes = payload_bytes[2:]
        return {"frame_type": 1, "ff_total_len": total_len, "data_bytes": data_bytes, "raw_payload": payload_bytes}
    # Consecutive Frame: 0x2n -> CF, data after b0
    if high_nibble == 0x2:
        data_bytes = payload_bytes[1:]
        seq = b0 & 0x0F
        return {"frame_type": 2, "seq": seq, "data_bytes": data_bytes, "raw_payload": payload_bytes}
    # Flow Control: 0x3x -> FC with fs (flow status) in low nibble
    if high_nibble == 0x3:
        fs = b0 & 0x0F
        # next bytes: block size, separation time, ...
        return {"frame_type": 3, "flow_status": fs, "data_bytes": payload_bytes[1:], "raw_payload": payload_bytes}
    # Heuristic fallback: sometimes PCI stripped so payload begins with UDS
    # If first byte is 0x7F or 0x62 etc, we treat whole as UDS payload
    return {"frame_type": None, "data_bytes": payload_bytes, "raw_payload": payload_bytes}