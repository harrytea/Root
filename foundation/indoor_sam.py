import torch
import numpy as np
from PIL import Image
from transformers import SamModel, SamProcessor
import os.path as osp
import os
import matplotlib.pyplot as plt
from tqdm import tqdm
import cv2
import json


class IndoorSAMEstimator:
    def __init__(self, config):
        self.config = config
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model, self.processor = self._init_sam_model()

    def _init_sam_model(self):
        print("Loading SAM model...")
        processor = SamProcessor.from_pretrained(self.config.sam_checkpoint)
        model = SamModel.from_pretrained(self.config.sam_checkpoint).to(self.device)
        return model, processor


    def _check_overlap(self, mask, exist_mask):
        """Check the overlap ratio between two masks."""
        intersection = np.logical_and(mask, exist_mask)
        overlap = intersection.sum() / float(mask.sum())
        return overlap


    def process_image(self, image_path, input_boxes):
        self.output_dir = self._prepare_output_dir(image_path)
        overlay_output_path = osp.join(self.output_dir, "mask_overlay.png")
        mask_output_path = osp.join(self.output_dir, "masks.npy")
        mask_selected_idx_path = osp.join(self.output_dir, "masks_selected_idx.json")

        # Check cache
        if self.config.use_cache and osp.exists(mask_selected_idx_path) and osp.exists(mask_output_path) and osp.exists(overlay_output_path):
            return self._load_cached_results(mask_selected_idx_path, mask_output_path)

        image = Image.open(image_path).convert("RGB")
        masks = []
        all_masks = []
        selected_boxes = []
        selected_idx = []
        for idx, box in enumerate(tqdm(input_boxes, desc="Processing masks")):
            current_mask = self._process_single_box(image, box)
            overlap_with_existing = False
            for existing_mask in masks:
                if self._check_overlap(current_mask, existing_mask) > 0.95:
                    overlap_with_existing = True
                    break

            if not overlap_with_existing:
                masks.append(current_mask)
                selected_boxes.append(box)
                selected_idx.append(idx)
            all_masks.append(current_mask)

        # Save results
        self._save_results(all_masks, selected_idx, mask_output_path, mask_selected_idx_path)
        self.visualize_masks(image_path, masks, overlay_output_path)
        return all_masks, selected_idx


    def _process_single_box(self, image, box):
        """Process a single bounding box and return its mask."""
        inputs = self.processor(image, input_boxes=[[box]], return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        masks = self.processor.image_processor.post_process_masks(
            outputs.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu()
        )
        mask = cv2.cvtColor(masks[0][0].numpy().squeeze().transpose((1, 2, 0)).astype(np.uint8), cv2.COLOR_BGR2GRAY)
        return mask > 0


    def visualize_masks(self, image_path, masks, output_path=None):
        image = Image.open(image_path).convert("RGB")
        plt.figure(figsize=(10, 10))
        plt.imshow(image)
        
        for mask in masks:  # masks[0] contains all masks for the first image
            self._show_mask(mask, plt.gca(), random_color=True)
        
        plt.axis('off')
        plt.savefig(output_path, bbox_inches='tight', pad_inches=0)
        plt.close()

    
    def _show_mask(self, mask, ax, random_color=False):
        if random_color:
            # 生成柔和的随机颜色
            hue = np.random.random()  # 随机色调
            rgb = plt.cm.hsv(hue)[:3]  # 转换为 RGB
            color = np.concatenate([rgb, np.array([0.6])], axis=0)  # alpha=0.4 更透明
        else:
            # 使用更柔和的蓝色
            color = np.array([0.2, 0.6, 1.0, 0.6])
        
        h, w = mask.shape[-2:]
        mask_image = mask.reshape(h, w, 1) * color.reshape(1, 1, -1)
        ax.imshow(mask_image)
        
        # 添加边界
        mask_binary = mask.astype(np.uint8) * 255
        kernel = np.ones((3,3), np.uint8)  # 调整kernel大小来控制边界粗细  (2,2)更细, (3,3)适中, (5,5)更粗
        eroded = cv2.erode(mask_binary, kernel)
        boundary = mask_binary - eroded
        
        # 创建半透明的边界
        boundary_image = np.zeros((h, w, 4))
        boundary_image[boundary > 0] = [1, 1, 1, 0.8]  # 白色边界，轻微透明
        ax.imshow(boundary_image)



    def _load_cached_results(self, mask_selected_idx_path, mask_output_path):
        """Load cached mask results if they exist."""
        with open(mask_selected_idx_path, 'r') as f:
            masks_info_dict = json.load(f)
        masks = np.load(mask_output_path)
        return masks, masks_info_dict["selected_idx"]



    def _save_results(self, all_masks, selected_idx, mask_output_path, mask_selected_idx_path):
        """Save masks and related information to disk."""
        np.save(mask_output_path, np.array(all_masks))
        selected_idx_dict = {"selected_idx": selected_idx}
        with open(mask_selected_idx_path, 'w') as f:
            json.dump(selected_idx_dict, f)


    def _prepare_output_dir(self, image_path):
        output_dir = osp.join(
            self.config.output_dir, 
            osp.basename(osp.dirname(image_path)), 
            osp.basename(image_path).split(".")[0]
        )
        os.makedirs(output_dir, exist_ok=True)
        return output_dir

if __name__ == "__main__":
    print()