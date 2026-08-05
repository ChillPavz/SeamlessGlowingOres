import os
import re
from PIL import Image

# Paths
INPUT_FILE = "final_7_color_palettes.txt"
OUTPUT_DIR = r"D:\Projects\DevCraft\Seamless Ores\seamless-glowing-ores\assets\borders"

def draw_border(colors, save_path):
    # Matches your exact math: 2 pixels of C1 at the ends, 1 of C7 in the center
    left_half = [colors[0], colors[0], colors[1], colors[2], colors[3], colors[4], colors[5], colors[6]]
    
    # Mirror it for the full 16-pixel edge
    edge_map = left_half + left_half[::-1]
    
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    pixels = img.load()
    
    for i in range(16):
        rgba_color = (*edge_map[i], 255)
        # Top and Bottom edges
        pixels[i, 0] = rgba_color
        pixels[i, 15] = rgba_color
        # Left and Right edges
        pixels[0, i] = rgba_color
        pixels[15, i] = rgba_color
        
    img.save(save_path)

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Cannot find {INPUT_FILE}. We need to curate the palettes first!")
        return
        
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        content = f.read()
        
    blocks = content.split("File: ")
    for block in blocks[1:]: # Skip empty split at the start
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        rel_path = lines[0] 
        
        # Extract the RGB values using regex
        colors = []
        for line in lines[1:]:
            match = re.search(r"RGB:\s*\((\d+),\s*(\d+),\s*(\d+)\)", line)
            if match:
                colors.append((int(match.group(1)), int(match.group(2)), int(match.group(3))))
        
        if len(colors) == 7:
            # Clean up names like diamond_ore_extracted.png -> diamond_ore_border.png
            base_name = os.path.splitext(rel_path)[0]
            base_name = (
                base_name
                .replace("_extracted", "")
                .replace("_PURE_ISOLATED", "")
            )

            base_name = re.sub(r"_overlay$", "", base_name)
            
            out_path = os.path.join(OUTPUT_DIR, f"{base_name}_border.png")
            
            # Ensure the proper subfolder exists
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            
            draw_border(colors, out_path)
            print(f"Generated: {out_path}")
        else:
            print(f"Skipping {rel_path} - Found {len(colors)} colors instead of exactly 7.")

if __name__ == "__main__":
    main()