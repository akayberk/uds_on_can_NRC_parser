# ║                                   ██████████                                   
# ║                           ██████████████████████████                           
# ║                      ████████████████████████████████████                      
# ║                    ████████████                █████████████                   
# ║                ███████████                          ███████████                
# ║              █████████                                  █████████              
# ║            ████████                                        ████████            
# ║          ████████                                            ████████          
# ║         ███████                                                ███████         
# ║       ███████                                                    ███████       
# ║      ██████                                                        ██████      
# ║     ██████                                                          ██████     
# ║    ██████                                                            ██████    
# ║    █████                                                              ██████   
# ║   █████                                                                █████   
# ║  ██████                                                                ██████  
# ║  █████                                                                  █████  
# ║ ██████ ████████     ████████       ████████        ████████     ███████ ██████ 
# ║ █████  ██████████ ██████████     ████████████      ███████████  ███████  █████ 
# ║ █████  █████████████████████    ██████████████     ████████████████████  █████ 
# ║ █████  █████████████████████   ████████ ███████    ████████████████████  █████ 
# ║ █████  █████████████████████  ██████████████████   ███████ ████████████  █████ 
# ║ █████  ███████  ██  ████████ ████████████████████  ███████   ██████████  █████ 
# ║ █████  ███████       ██████ ███████        ███████ ██████       ███████  █████ 
# ║────────────────────────────────────────────────────────────────────────────║
# ║  PROJECT : UDS on CAN Parser / Diagnostic Log Analyzer                     ║
# ║  AUTHOR  : <Berk Yaşar Akay> (Test Intern @ MAN)                           ║
# ║  CREATED : 2025-10-13                                                      ║
# ║  VERSION : v1.2.2                                                          ║
# ║────────────────────────────────────────────────────────────────────────────║
# ║  DESCRIPTION:                                                              ║
# ║     > Reads raw .ASC CAN logs                                              ║
# ║     > Matches TX ↔ RX frames                                               ║
# ║     > Decodes UDS SIDs & NRCs to human-readable form                       ║
# ║     > Multilingual GUI (EN / TR / DE)                                      ║
# ║     > Exports parsed data to CSV                                           ║
# ║────────────────────────────────────────────────────────────────────────────║
# ║  (c) MAN Truck & Bus SE  —  Engineering Division  —  Confidential Build    ║
# ╚════════════════════════════════════════════════════════════════════════════╝


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UDS-on-CAN ASC Parser — v2
- ISO-TP reassembly (SF/FF/CF/FC) with Flow Control handling (FC ignored except for pacing)
- CAN-ID filter: only process frames whose CAN ID starts with 18DA (case-insensitive)
- TesterPresent (0x3E) fully ignored
- Support for "suppress positive" flag: if Tx contains 0x3E and second byte has 0x80 set (3E 80),
  then positive responses will be ignored for that Tx; negative responses are still shown.
- If multiple 0x78 ResponsePending occur, record first and last; show only first+last, suppress middle.
- Reassembled positive multi-frame payloads are converted to ASCII where appropriate (e.g., VIN).
- GUI highlights positives (green) and negatives (red). ResponsePending shows as yellow.
- Drop-in replacement for previous script: only needs standard library (tkinter).
"""
import re
import os
import sys
import csv
from collections import defaultdict
from datetime import datetime
from uds_codes import UDS_SERVICES, NRC_MAP, DID_MAP
from parser import AscParser, TRANSLATIONS
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception as e:
    print("Tkinter required for GUI. Install/enable Tkinter.", e)
    sys.exit(1)

def tr(lang, key, **kwargs):
    txt = TRANSLATIONS.get(lang, TRANSLATIONS["EN"]).get(key, "")
    if kwargs:
        try:
            return txt.format(**kwargs)
        except Exception:
            return txt
    return txt


# ----------------------------
# GUI App
# ----------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.lang = tk.StringVar(value="TR") #inital program language
        self._build_language_dialog()
        self.parser = AscParser(language=self.lang.get())
        self.root.title(tr(self.lang.get(), "title")) #gives the title of the app by selected language.
        self._build_main_ui()

    def _build_language_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Language / Dil / Sprache")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Choose language / Dil seçiniz / Sprache wählen:").pack(padx=12, pady=6)
        f = tk.Frame(dlg)
        f.pack(padx=6, pady=6)
        tk.Radiobutton(f, text="English", variable=self.lang, value="EN").pack(side="left", padx=6)
        tk.Radiobutton(f, text="Türkçe", variable=self.lang, value="TR").pack(side="left", padx=6)
        tk.Radiobutton(f, text="Deutsch", variable=self.lang, value="DE").pack(side="left", padx=6)
        b = tk.Button(dlg, text="OK", command=dlg.destroy)
        b.pack(pady=8)
        self.root.wait_window(dlg)

    def _build_main_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="none") #x for aling to left, y for aling to center,(for this instence only) also "none" alings to center
        open_btn = ttk.Button(top, text=tr(self.lang.get(), "open_btn"), command=self._open_file_dialog)
        open_btn.pack(side="left", padx=6)
        export_btn = ttk.Button(top, text=tr(self.lang.get(), "export_btn"), command=self._export_csv)
        export_btn.pack(side="left", padx=6)

        mid = ttk.Frame(self.root, padding=4) #alings the space between buttons and the list section.
        mid.pack(fill="both", expand=True) #expands the list section to the given direction. x,y,none or both
        self.text = tk.Text(mid, wrap="none")
        self.text.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.text.yview)
        vsb.pack(side="right", fill="y")
        hsb = ttk.Scrollbar(self.root, orient="horizontal", command=self.text.xview)
        hsb.pack(fill="x")
        self.text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        # tags for coloring
        self.text.tag_configure("positive", background="#aaffaa")  # light green
        self.text.tag_configure("negative", background="#ffaaaa")  # light red
        self.text.tag_configure("pending", background="#e2bb4e")   # light orange
        self.file_label = ttk.Label(self.root, text=tr(self.lang.get(), "no_file"))
        self.file_label.pack(fill="x", padx=6, pady=4)

    def _open_file_dialog(self):
        fn = filedialog.askopenfilename(filetypes=[("ASC files", "*.asc")])
        if fn:
            self._load_file(fn)

    def _load_file(self, path):
        if not os.path.isfile(path):
            messagebox.showerror("Error", "File not found")
            return
        if not path.lower().endswith(".asc"):
            messagebox.showerror("Error", "Only .asc files are accepted")
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Error reading", str(e))
            return
        self.current_file = path
        self.file_label.configure(text=tr(self.lang.get(), "file_loaded", path=path, lines=len(content.splitlines())))
        self.text.delete("1.0", "end")
        self.text.insert("1.0", tr(self.lang.get(), "file_loaded", path=path, lines=len(content.splitlines())))
        self._last_loaded_content = content
        self._auto_parse_after_load()

    def _auto_parse_after_load(self):
        try:
            self.parser.ingest(self._last_loaded_content)
            self.parser.pair_tx_rx()
            results = self.parser.interpret_all_pairs()
            self.text.insert("end", tr(self.lang.get(), "tx_count", tx=len(self.parser.tx_entries), rx=len(self.parser.rx_entries)) + "\n")
            self.text.insert("end", tr(self.lang.get(), "paired_summary", paired=len(results), utx=len(self.parser.unpaired_tx), urx=len(self.parser.unpaired_rx)) + "\n\n")
            # display each result; use tags for coloring
            for r in results:
                line = self._format_result_line(r)
                start = self.text.index("end-1c")
                self.text.insert("end", line + "\n")
                end = self.text.index("end-1c")
                # apply tag based on negative/positive/pending
                if r["is_negative"]:
                    self.text.tag_add("negative", start, end)
                elif r["ascii"]:
                    # treat positive with ascii as positive
                    self.text.tag_add("positive", start, end)
                elif r["first_78"] or r["last_78"]:
                    self.text.tag_add("pending", start, end)
            # show some unpaired samples
            if self.parser.unpaired_tx:
                self.text.insert("end", "\n--- Unpaired TX (sample) ---\n")
                for utx in self.parser.unpaired_tx[:5]:
                    self.text.insert("end", utx["raw_line"] + "\n")
            if self.parser.unpaired_rx:
                self.text.insert("end", "\n--- Unpaired RX (sample) ---\n")
                for urx in self.parser.unpaired_rx[:5]:
                    self.text.insert("end", urx["raw_line"] + "\n")
            self.text.insert("end", "\n" + tr(self.lang.get(), "parsing_complete") + "\n")
        except Exception as e:
            messagebox.showerror("Parse error", str(e))

    def _format_result_line(self, r):
        tx_ts = f"{r['tx_time']:.6f}"
        rx_ts = f"{r['rx_time']:.6f}" if r["rx_time"] else "N/A"
        sid = f"{r['service_name']}"
        did_info = f" DID:{r['did_name']} (0x{r['did']:04X})" if r["did"] else ""
        if r["is_negative"]:
            nrc_text = r["nrc_text"] or (f"0x{r['nrc']:02X}" if r["nrc"] is not None else "UnknownNRC")
            extra = ""
            # show first/last 0x78 metadata if present
            if r.get("first_78") and r.get("last_78"):
                extra = f" [ResponsePending first/last present]"
            return f"[TX {tx_ts}] {r['tx_can_id']} -> [RX {rx_ts}] {r['rx_can_id']} | {sid} | {tr(self.lang.get(), 'negative_label')}: {nrc_text}| TX:{r['tx_payload']} RX:{r['rx_payload']}"
        else:
            # positive: if ascii available show it
            ascii_part = f" ASCII:'{r['ascii']}'" if r['ascii'] else ""
            return f"[TX {tx_ts}] {r['tx_can_id']} -> [RX {rx_ts}] {r['rx_can_id']} | {sid}{did_info} | TX:{r['tx_payload']} RX:{r['rx_payload']}"

    def _export_csv(self):
        if not hasattr(self, "current_file") or not self.current_file:
            messagebox.showwarning("No file", "Load a .asc file first")
            return
        res = self.parser.interpret_all_pairs()
        if not res:
            messagebox.showinfo("No data", "No paired data to export")
            return
        out = os.path.splitext(self.current_file)[0] + "_parsed.csv"
        try:
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["tx_time", "rx_time", "tx_can_id", "rx_can_id", "service_sid", "service_name", "did", "did_name", "is_negative", "nrc", "nrc_text", "ascii", "tx_payload", "rx_payload", "first_78", "last_78"])
                for r in res:
                    w.writerow([
                        f"{r['tx_time']:.6f}",
                        f"{r['rx_time']:.6f}" if r['rx_time'] else "",
                        r["tx_can_id"],
                        r["rx_can_id"],
                        (f"0x{r['service_sid']:02X}" if r['service_sid'] is not None else ""),
                        r["service_name"],
                        (f"0x{r['did']:04X}" if r['did'] is not None else ""),
                        r["did_name"],
                        "YES" if r["is_negative"] else "NO",
                        (f"0x{r['nrc']:02X}" if r['nrc'] is not None else ""),
                        r["nrc_text"],
                        r["ascii"],
                        r["tx_payload"],
                        r["rx_payload"],
                        r.get("first_78", ""),
                        r.get("last_78", ""),
                    ])
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        messagebox.showinfo("Exported", f"CSV written to: {out}")


# ----------------------------
# Entrypoint
# ----------------------------
def main():
    root = tk.Tk()
    root.minsize(900, 700)
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()