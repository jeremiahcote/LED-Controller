# pythonLED_gui_ctk.py
# Modern GUI using customtkinter for LEDControllerWindows.py


# Must be first lines (helps Bleak on Windows GUI apps)
import sys
sys.coinit_flags = 0
try:
   from bleak.backends.winrt.util import uninitialize_sta
   try:
       uninitialize_sta()
   except Exception:
       pass
except Exception:
   pass


import threading
import asyncio
import customtkinter as ctk
from tkinter import colorchooser  # ok to mix for color picker


import LEDControllerWindows  # your backend (must NOT auto-run on import)




PALETTE = [
   ("Red",    "#ff0000"),
   ("Orange", "#ec5800"),
   ("Yellow", "#FFAF00"),
   ("Green",  "#00ff00"),
   ("Blue",   "#0000ff"),
   ("Purple", "#800080"),
   ("Cyan",   "#00ffff"),
   ("Pink",   "#ff2dd6"),
   ("White",  "#ffffff")
]




def hex_to_rgb(h: str):
   h = h.lstrip("#")
   return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)




class App(ctk.CTk):
   def __init__(self):
       super().__init__()


       ctk.set_appearance_mode("dark")
       ctk.set_default_color_theme("blue")


       # Fonts (Cinzel everywhere)
       self.font_title = ctk.CTkFont(family="Cinzel", size=26, weight="bold")
       self.font_label = ctk.CTkFont(family="Cinzel", size=14)
       self.font_btn = ctk.CTkFont(family="Cinzel", size=14, weight="bold")
       self.font_small = ctk.CTkFont(family="Cinzel", size=14)


       self.title("LED Control")
       self.geometry("420x700")
       self.minsize(420, 700)


       self.power = ctk.StringVar(value="on")
       self.color_hex = ctk.StringVar(value="#ffffff")
       self.brightness = ctk.IntVar(value=100)


       # Header
       header = ctk.CTkFrame(self, corner_radius=18)
       header.pack(fill="x", padx=16, pady=(16, 10))


       title = ctk.CTkLabel(
           header,
           text="LED Control",
           font=self.font_title
       )
       title.pack(anchor="w", padx=16, pady=(14, 2))


       subtitle = ctk.CTkLabel(
           header,
           text="LED strips",
           text_color="#9aa7bd",
           font=self.font_small
       )
       subtitle.pack(anchor="w", padx=16, pady=(0, 14))


       # Main card
       card = ctk.CTkFrame(self, corner_radius=18)
       card.pack(fill="both", expand=True, padx=16, pady=(0, 12))


       # Power segmented control (On/Off)
       power_frame = ctk.CTkFrame(card, corner_radius=14)
       power_frame.pack(fill="x", padx=16, pady=(16, 10))


       ctk.CTkLabel(
           power_frame,
           text="Power",
           text_color="#9aa7bd",
           font=self.font_label
       ).pack(anchor="w", padx=14, pady=(12, 6))


       seg = ctk.CTkFrame(power_frame, corner_radius=14)
       seg.pack(fill="x", padx=14, pady=(0, 12))


       self.btn_on = ctk.CTkButton(
           seg,
           text="On",
           height=38,
           corner_radius=14,
           command=lambda: self._set_power("on"),
           font=self.font_btn
       )
       self.btn_on.pack(side="left", expand=True, fill="x", padx=(0, 6), pady=6)


       self.btn_off = ctk.CTkButton(
           seg,
           text="Off",
           height=38,
           corner_radius=14,
           command=lambda: self._set_power("off"),
           font=self.font_btn
       )
       self.btn_off.pack(side="left", expand=True, fill="x", padx=(6, 0), pady=6)


       # Color preview + palette
       color_frame = ctk.CTkFrame(card, corner_radius=14)
       color_frame.pack(fill="x", padx=16, pady=10)


       ctk.CTkLabel(
           color_frame,
           text="Color",
           text_color="#9aa7bd",
           font=self.font_label
       ).pack(anchor="w", padx=14, pady=(12, 6))


       self.preview = ctk.CTkFrame(color_frame, height=34, corner_radius=12)
       self.preview.pack(fill="x", padx=14, pady=(0, 10))
       self._update_preview()


       grid = ctk.CTkFrame(color_frame, corner_radius=0, fg_color="transparent")
       grid.pack(fill="x", padx=10, pady=(0, 12))


       # 4 columns of color buttons
       cols = 3


       for i, (name, hx) in enumerate(PALETTE):
           rr = i // cols
           cc = i % cols


           b = ctk.CTkButton(
               grid,
               text=name,
               height=38,
               corner_radius=14,
               command=lambda hx=hx: self._set_color(hx),
               font=self.font_btn,  # bold
           )
           b.grid(row=rr, column=cc, padx=6, pady=6, sticky="ew")


       # make all columns expand evenly
       for cc in range(cols):
           grid.grid_columnconfigure(cc, weight=1)


       custom_btn = ctk.CTkButton(
           color_frame,
           text="Custom Color…",
           height=38,
           corner_radius=14,
           command=self._custom_color,
           font=self.font_btn
       )
       custom_btn.pack(fill="x", padx=14, pady=(0, 14))


       # Status + Apply
       bottom = ctk.CTkFrame(card, corner_radius=14)
       bottom.pack(fill="x", padx=16, pady=(10, 16))


       self.status = ctk.CTkLabel(
           bottom,
           text="",
           text_color="#9aa7bd",
           font=self.font_small
       )
       self.status.pack(anchor="w", padx=14, pady=(12, 6))


       self.apply_btn = ctk.CTkButton(
           bottom,
           text="Apply",
           height=50,
           corner_radius=16,
           command=self._apply,
           font=ctk.CTkFont(family="Cinzel", size=18, weight="bold")
       )
       self.apply_btn.pack(fill="x", padx=14, pady=(0, 14))


       self._sync_power_ui()


   def _set_power(self, value: str):
       self.power.set(value)
       self._sync_power_ui()


   def _sync_power_ui(self):
       # Make one look "selected"
       if self.power.get() == "on":
           self.btn_on.configure(state="normal")
           self.btn_off.configure(state="normal")
           self.btn_on.configure(fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"])
           self.btn_off.configure(fg_color=("gray25", "gray20"))
       else:
           self.btn_on.configure(state="normal")
           self.btn_off.configure(state="normal")
           self.btn_off.configure(fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"])
           self.btn_on.configure(fg_color=("gray25", "gray20"))


   def _set_color(self, hx: str):
       self.color_hex.set(hx)
       self._update_preview()


   def _custom_color(self):
       chosen = colorchooser.askcolor(title="Pick a color", initialcolor=self.color_hex.get())
       if chosen and chosen[1]:
           self._set_color(chosen[1])


   def _update_preview(self):
       # Update preview frame color
       self.preview.configure(fg_color=self.color_hex.get())


   def _on_slider(self, val):
       v = int(val)
       self.brightness.set(v)
       self.brightness_pill.configure(text=str(v))


   def _set_status(self, msg: str, is_error: bool = False):
       self.status.configure(text=msg, text_color=("#ff8a8a" if is_error else "#9aa7bd"))


   def _apply(self):
    power = self.power.get()
    r, g, b = hex_to_rgb(self.color_hex.get())
    brightness = int(self.brightness.get())

    self.apply_btn.configure(state="disabled", text="Applying...")
    self._set_status("Connecting to LEDs...")

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            coro = LEDControllerWindows.apply_from_gui(
                power,
                r,
                g,
                b,
                brightness
            )

            loop.run_until_complete(
                asyncio.wait_for(coro, timeout=20.0)
            )

        except asyncio.TimeoutError:
            self.after(
                0,
                lambda: self._set_status(
                    "Bluetooth timed out. Make sure both LEDs are powered on.",
                    True
                )
            )

        except Exception as e:
            error_message = str(e)

            self.after(
                0,
                lambda msg=error_message: self._set_status(
                    f"Error: {msg}",
                    True
                )
            )

        else:
            self.after(
                0,
                lambda: self._set_status("Applied.")
            )

        finally:
            self.after(
                0,
                lambda: self.apply_btn.configure(
                    state="normal",
                    text="Apply"
                )
            )

            try:
                loop.close()
            except Exception:
                pass

    threading.Thread(
        target=worker,
        daemon=True
    ).start()




if __name__ == "__main__":
   App().mainloop()
