from PIL import Image, ImageChops

# 打开原图和转化后的WebP
png_img = Image.open("test.png")
webp_img = Image.open("test.webp").convert("RGBA")  # 对齐PNG的RGBA格式

# 对比像素差异
diff = ImageChops.difference(png_img, webp_img)
if diff.getbbox() is None:
    print("✅ 转化无损（像素完全一致）")
else:
    print("❌ 转化有像素差异（非无损）")