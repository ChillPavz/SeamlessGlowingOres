import os
from PIL import Image

# Your overlays directory
BASE_DIR = r"D:\Projects\DevCraft\Seamless Ores\seamless-glowing-ores\assets\overlays"
OUTPUT_FILE = "extracted_palettes.txt"

def get_luminance(rgb):
    # Sorts colors naturally by perceived brightness
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

def main():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for root, _, files in os.walk(BASE_DIR):
            for file in files:
                if file.endswith(".png"):
                    file_path = os.path.join(root, file)
                    # Keeps the folder structure (e.g., create\zinc_ore.png)
                    rel_path = os.path.relpath(file_path, BASE_DIR)
                    
                    try:
                        img = Image.open(file_path).convert("RGBA")
                        colors = set()
                        
                        for pixel in img.getdata():
                            if pixel[3] > 0:  # Ignore transparent background
                                colors.add(pixel[:3])
                                
                        # Sort by brightness from darkest to lightest
                        sorted_colors = sorted(list(colors), key=get_luminance)
                        
                        f.write(f"File: {rel_path}\n")
                        for i, color in enumerate(sorted_colors, 1):
                            hex_color = "#{:02x}{:02x}{:02x}".format(*color).upper()
                            f.write(f"{i:02}. {hex_color}  |  RGB: {color}\n")
                        f.write("\n")
                        
                    except Exception as e:
                        print(f"Error processing {rel_path}: {e}")

    print(f"Extraction complete! Open {OUTPUT_FILE} to grab the lists.")

if __name__ == "__main__":
    main()