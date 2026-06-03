"""Build staff appearance embedding bank.

Two modes:
  Interactive:  python build_staff_bank.py --clips clips/
                OpenCV window shows each person. Press S=staff, N=skip, Q=quit.
  Auto : python build_staff_bank.py --clips clips/ --auto
                Saves all unique persons as candidate_NNNN.npy + .jpg thumbnail.
                You then rename staff ones: mv candidate_0002.npy staff_0002.npy
"""

import argparse, logging, os, sys, uuid
import cv2, numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PERSON_CLASS = 0


def get_embedding(crop):
    try:
        import torchreid
        ext = torchreid.utils.FeatureExtractor(model_name="osnet_x0_25", device="cpu")
        r = cv2.resize(crop, (128, 256))
        feat = ext([cv2.cvtColor(r, cv2.COLOR_BGR2RGB)])
        return feat.cpu().numpy().flatten()
    except Exception:
        r = cv2.resize(crop, (64, 128))
        h = cv2.cvtColor(r, cv2.COLOR_BGR2HSV)
        f = np.concatenate([
            cv2.calcHist([h],[0],None,[32],[0,180]).flatten(),
            cv2.calcHist([h],[1],None,[32],[0,256]).flatten(),
            cv2.calcHist([h],[2],None,[16],[0,256]).flatten(),
        ])
        n = np.linalg.norm(f)
        return f / n if n > 0 else f


def get_model():
    try:
        from ultralytics import YOLO
        return YOLO("yolov8n.pt")
    except ImportError:
        logger.error("pip install ultralytics")
        sys.exit(1)


def auto_sample(clips_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    model = get_model()
    seen, saved = set(), 0
    for fname in sorted(os.listdir(clips_dir)):
        if not fname.lower().endswith((".mp4",".avi",".mov",".mkv")):
            continue
        cap = cv2.VideoCapture(os.path.join(clips_dir, fname))
        fps = cap.get(cv2.CAP_PROP_FPS) or 15
        fi, max_fi = 0, int(fps * 120)
        while fi < max_fi:
            ret, frame = cap.read()
            if not ret: break
            fi += 1
            if fi % 30 != 0: continue
            res = model.track(frame, classes=[PERSON_CLASS], conf=0.4, persist=True, verbose=False)
            if not res or res[0].boxes is None or res[0].boxes.id is None: continue
            for i, tid in enumerate(res[0].boxes.id.int().tolist()):
                if tid in seen: continue
                seen.add(tid)
                b = res[0].boxes.xyxy[i].tolist()
                x1,y1,x2,y2 = [int(v) for v in b]
                crop = frame[max(0,y1):y2, max(0,x1):x2]
                if crop.size == 0: continue
                np.save(f"{out_dir}/candidate_{tid:04d}.npy", get_embedding(crop))
                cv2.imwrite(f"{out_dir}/candidate_{tid:04d}.jpg", cv2.resize(crop,(80,160)))
                saved += 1
        cap.release()
    logger.info(f"Saved {saved} candidates to {out_dir}/")
    logger.info("Review .jpg files and rename staff ones: mv candidate_0002.npy staff_0002.npy")
    logger.info("Delete non-staff candidate_*.npy files.")


def interactive(clips_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    model = get_model()
    saved = 0
    for fname in sorted(os.listdir(clips_dir)):
        if not fname.lower().endswith((".mp4",".avi",".mov",".mkv")):
            continue
        cap = cv2.VideoCapture(os.path.join(clips_dir, fname))
        fi = 0
        while fi < 300:
            ret, frame = cap.read()
            if not ret: break
            fi += 1
            if fi % 15 != 0: continue
            res = model(frame, classes=[PERSON_CLASS], conf=0.4, verbose=False)
            if not res or res[0].boxes is None: continue
            for box in res[0].boxes.xyxy.tolist():
                x1,y1,x2,y2 = [int(v) for v in box]
                crop = frame[max(0,y1):y2, max(0,x1):x2]
                if crop.size == 0: continue
                show = cv2.resize(crop, (200, 400))
                cv2.putText(show, "S=Staff  N=Skip  Q=Quit", (5,20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)
                cv2.imshow("Staff Labeller", show)
                key = cv2.waitKey(0) & 0xFF
                if key == ord('s'):
                    p = f"{out_dir}/staff_{uuid.uuid4().hex[:8]}.npy"
                    np.save(p, get_embedding(crop))
                    saved += 1
                    logger.info(f"Saved staff #{saved}: {p}")
                elif key == ord('q'):
                    cap.release()
                    cv2.destroyAllWindows()
                    return
        cap.release()
    cv2.destroyAllWindows()
    logger.info(f"Done — {saved} staff embeddings in {out_dir}/")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--clips",  default="clips/")
    p.add_argument("--output", default="staff_embeddings/")
    p.add_argument("--auto",   action="store_true")
    args = p.parse_args()
    auto_sample(args.clips, args.output) if args.auto else interactive(args.clips, args.output)
