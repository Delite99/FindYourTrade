"""Dev helper: generate a short Show Me test clip (4 bags + code word)."""
import cv2
import numpy as np

W, H = 640, 480
vw = cv2.VideoWriter("test_showme.mp4", cv2.VideoWriter_fourcc(*"mp4v"), 10, (W, H))
for _ in range(30):
    img = np.full((H, W, 3), 40, dtype=np.uint8)
    for b in range(4):
        x = 60 + b * 130
        cv2.rectangle(img, (x, 300), (x + 90, 400), (70, 140, 220), -1)
        cv2.putText(img, str(b + 1), (x + 30, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(img, "mango-27", (180, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    vw.write(img)
vw.release()
print("created test_showme.mp4")
