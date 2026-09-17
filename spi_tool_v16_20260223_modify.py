# Date: 2026/9/17
import tkinter as tk  # 用來建立GUI
from tkinter import messagebox, filedialog 
from pyftdi.spi import SpiController   # FT232H 驅動
import crcmod   # CRC 計算
import re  # 正規表示式操作,用來比對資料是否匹配
import time
import threading

# ============================
# CRC16 Setup
# ============================
crc16 = crcmod.mkCrcFun(0x11021, initCrc=0xFFFF, rev=False)

class SPIGUI:
    def __init__(self, master):
        self.master = master
        master.title("FT232H + LP5899 (v16.0 Independent Packets & Aligned)")
        master.geometry("900x650")

        self.ctrl = None 
        self.stop_flag = False 
        
        # ===== GUI Layout =====
        tk.Label(master, text="SPI Clock (Hz):").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        
        self.valid_freqs = [
            "3000000",  # 3 MHz (推薦)
            "6000000",  # 6 MHz
            "10000000",
            "12000000", 
            "15000000",   
            "20000000"  # 20 MHz
        ]
        self.freq_var = tk.StringVar(value="3000000") 
        tk.OptionMenu(master, self.freq_var, *self.valid_freqs).grid(row=0, column=1, sticky="w", padx=5)
        tk.Label(master, text="(Recommend 3MHz)", fg="#666").grid(row=0, column=2, sticky="w")

        tk.Label(master, text="SPI Mode:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.spi_mode_var = tk.StringVar(value="0")
        tk.OptionMenu(master, self.spi_mode_var, "0", "1", "2", "3").grid(row=1, column=1, sticky="w", padx=5)

        tk.Label(master, text="Manual Data (Hex):").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.data_entry = tk.Entry(master, width=60) 
        self.data_entry.grid(row=2, column=1, sticky="w", padx=5)

        tk.Label(master, text="Loop Count (0=Inf):").grid(row=3, column=0, sticky="e", padx=5, pady=5)
        self.loop_entry = tk.Entry(master, width=10)
        self.loop_entry.insert(0, "0") 
        self.loop_entry.grid(row=3, column=1, sticky="w", padx=5)
        tk.Label(master, text="(Enter 0 for Infinite Loop)", fg="blue").grid(row=3, column=1, padx=80, sticky="w")

        # ===== Buttons =====
        btn_frame = tk.Frame(master)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=15)
        
        tk.Button(btn_frame, text="1. Connect FTDI", command=self.connect_spi, bg="#dddddd", width=15).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="2. Init System", command=self.perform_initialization, bg="#ffcc88", width=15).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="3. Manual Write", command=self.gui_write, bg="#aaddaa", width=15).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="4. Play TXT (Loop)", command=self.start_play_thread, bg="#aaaaff", width=15).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Stop Loop", command=self.stop_loop, bg="#ff8888", width=10).pack(side=tk.LEFT, padx=20)

        self.output = tk.Text(master, height=22, width=110, bg="#f4f4f4")
        self.output.grid(row=5, column=0, columnspan=3, padx=10, pady=10)
        self.log("Ready. v16.0: CS Toggle Enforced for each 64-pixel packet.")

    # ============================
    # 1. CONNECT
    # ============================
    def connect_spi(self):
        try:
            if self.ctrl: self.ctrl.terminate()
            self.ctrl = SpiController()
            self.ctrl.configure('ftdi://ftdi:232h/1')
            
            try:
                ftdi = self.ctrl._ftdi
                #ftdi.set_latency_timer(1) # 關鍵：縮短封包與封包之間的作業系統延遲
                ftdi.set_latency_timer(4) # 關鍵：縮短封包與封包之間的作業系統延遲 _20260521 ftdi.set_latency_timer(2)
                self.log(f"FTDI Latency Timer set to 2ms.")
            except Exception as e:
                self.log(f"Warning: Latency setup failed: {e}")

            self.log("FTDI Connected.")
        except Exception as e:
            self.log(f"Conn Error: {e}")

    def get_port(self):
        if not self.ctrl: raise Exception("Not Connected")
        return self.ctrl.get_port(cs=0, freq=int(self.freq_var.get()), mode=int(self.spi_mode_var.get()))

    def build_packet(self, data_bytes): 
        crc = crc16(data_bytes)
        return data_bytes + crc.to_bytes(2, 'big')

    # ============================
    # 3. INITIALIZATION
    # ============================
    def perform_initialization(self):
        if not self.ctrl:
            messagebox.showerror("Error", "Please Connect FTDI first.")
            return

        lp5899_cmds = [
            (b'\xA0\x40\x30\x00', "LP5899: Disable WDT"),
            (b'\xA1\xC0\x80\x00', "LP5899: Clear POR"),
            (b'\xA1\x40\x00\x01', "LP5899: Exit Failsafe"),
            (b'\xA0\x80\x00\x0D', "LP5899: Set Rate 10M")
        ]
        
        lp5891_cmds = [
            (b'\x20\x00\xAA\x10', "LP5891: Reset"),
            (b'\x20\x03\xAA\x00\x20\x01\x10\xC4\x01\x03', "LP5891: FC0"),   # \x20\x03\xAA\x00\x20\x00\x30\xCF\x01\x0F
            (b'\x20\x03\xAA\x01\x2A\x10\x00\x94\xA6\x31', "LP5891: FC1"),   # \x20\x03\xAA\x01\x00\x00\x00\x94\xA5\xFF
            (b'\x20\x03\xAA\x02\x03\x80\x00\x00\x06\x66', "LP5891: FC2"),   # \x20\x03\xAA\x02\x00\x00\x00\x00\x06\x66
            (b'\x20\x03\xAA\x03\x00\x3B\x7F\x7F\x7F\x00', "LP5891: FC3"),   # \x20\x03\xAA\x03\x00\x3B\x7F\x7F\x7F\x00
            (b'\x20\x03\xAA\x04\x08\x00\x0F\xF8\x40\x3E', "LP5891: FC4")    # \x20\x03\xAA\x04\x08\x00\x0F\xF8\x40\x3E
        ]

    

        try:
            spi = self.get_port()
            self.log("=== Init System ===")
            time.sleep(0.01)

            for payload, desc in lp5899_cmds:
                tx_pkt = self.build_packet(payload) 
                spi.exchange(tx_pkt + b'\xFF'*(len(tx_pkt)+2), duplex=False)
                self.log(f"Sent: {desc}")
                #time.sleep(0.002)

            #time.sleep(0.005)

            for payload, desc in lp5891_cmds:
                tx_pkt = self.build_packet(payload)
                spi.exchange(tx_pkt + b'\xFF'*(len(tx_pkt)+2), duplex=False)
                self.log(f"Sent: {desc}")
                #time.sleep(0.002)

            self.log("Init Done.")
            messagebox.showinfo("Success", "System Initialized.")

        except Exception as e:
            self.log(f"Init Error: {e}")

    # ============================
    # 4. MANUAL WRITE
    # ============================
    def gui_write(self):
        try:
            d_str = self.data_entry.get().strip()
            if not d_str: return
            payload = bytearray()
            for x in d_str.split():
                val = int(x, 16)
                if len(x) > 2 or val > 0xFF: payload.extend(val.to_bytes(2, 'big'))
                else: payload.extend(val.to_bytes(1, 'big'))
            tx = self.build_packet(payload)
            rx = self.get_port().exchange(tx + b'\xFF'*(len(tx)+2), duplex=True)
            self.log(f"TX: {tx.hex(' ').upper()}")
            self.log(f"RX: {rx[len(tx):].hex(' ').upper()}")
        except Exception as e: self.log(f"Error: {e}")

    # ============================
    # 5. THREAD CONTROL
    # ============================
    def start_play_thread(self):
        self.stop_flag = False
        t = threading.Thread(target=self.send_from_text_file)
        t.daemon = True 
        t.start()

    def stop_loop(self):
        self.stop_flag = True
        self.log(">>> Stop Signal Sent...")

    # ============================
    # 6. SEND LOGIC (v16.0 CS Toggle Fixed)
    # ============================
    def send_from_text_file(self):
        if not self.ctrl: 
            self.log("Error: FTDI not connected.")
            return
            
        path = filedialog.askopenfilename()
        if not path: return
        
        try:
            loop_cnt = int(self.loop_entry.get())
        except:
            loop_cnt = 1
        
        try:
            with open(path, "r") as f: tokens = re.split(r'[,\s\n\t]+', f.read())
            raw_vals = [int(t, 16) for t in tokens if t]
            
            if not raw_vals: 
                self.log("Error: Empty file.")
                return
            
            spi = self.get_port()
            
            # --- Pre-calculation ---
            self.log("Building Packets (64 Pixels/Packet)...")
            
            #PIXELS_PER_PACKET = 64 
            PIXELS_PER_PACKET = 30
            packet_list = [] # 將每個獨立封包存在 list 裡
            
            total_pixels = len(raw_vals) // 3
            total_processed = 0
            
            # 1. 製造影像封包 (Data Packets)
            while total_processed < total_pixels:
                remaining = total_pixels - total_processed
                count = min(remaining, PIXELS_PER_PACKET)
                
                chunk = raw_vals[total_processed*3 : (total_processed+count)*3]
                
                inner = bytearray()
                for j in range(0, len(chunk), 3):
                    p = chunk[j:j+3]
                    inner.extend(b'\xAA\x30')
                    for v in p: inner.extend(v.to_bytes(2, 'big'))
                
                word_count = len(inner) // 2
                length_val = word_count - 1
                
                if length_val > 255: 
                    self.log(f"Error: Packet too big {length_val}")
                    return

                cmd = 0x2000 | length_val 
                header = cmd.to_bytes(2, 'big')
                
                # 完成一個獨立的封包
                pkt = self.build_packet(header + inner)
                packet_list.append(pkt)
                
                total_processed += count

            # 2. 製造 VSYNC 封包 (獨立的一包)
            vsync_pkt = self.build_packet(b'\x20\x00\xAA\xF0')
            packet_list.append(vsync_pkt)
            
            self.log(f"Ready. Packets generated: {len(packet_list)}")
            self.log(">>> STARTING LOOP (With CS Toggles) <<<")
            
            current_loop = 0
            
            while True:
                if self.stop_flag: 
                    self.log("Loop Stopped.")
                    break
                if loop_cnt != 0 and current_loop >= loop_cnt:
                    self.log("Loop Finished.")
                    break

                current_loop += 1
                if current_loop % 500 == 1: 
                    self.log(f"Loop #{current_loop}...")

                # ==========================================
                # 【關鍵修改】使用 for 迴圈獨立發送每個封包
                # 每呼叫一次 spi.write，FTDI 就會執行:
                # 1. CS 降為 Low
                # 2. 傳送該封包的 bytes (包含該包自己的 CRC)
                # 3. CS 升為 High (讓 LP5899 驗證並執行)
                # ==========================================
                for pkt in packet_list:
                    spi.write(pkt)
                
                # Frame 與 Frame 之間的微小延遲 (避免洗頻率太高)
                # 您可以依需求註解掉或修改
                # time.sleep(0.005) 
            
            messagebox.showinfo("Done", f"Finished {current_loop} loops.")
            
        except Exception as e:
            self.log(f"Error: {e}")
            print(f"Error: {e}")

    def log(self, msg):
        try:
            self.output.insert(tk.END, msg+"\n")
            self.output.see(tk.END)
        except:
            pass

if __name__ == "__main__":
    root = tk.Tk()
    SPIGUI(root)
    root.mainloop()
