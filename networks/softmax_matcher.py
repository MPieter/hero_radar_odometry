import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

class SoftmaxMatcher(nn.Module):
    def __init__(self, config):
        super(SoftmaxMatcher, self).__init__()
        self.softmax_temp = config["networks"]["matcher_block"]["softmax_temp"]
        self.window_size = config["window_size"]
        self.gpuid = config["gpuid"]

    def forward(self, keypoint_scores, keypoint_desc, scores_dense, desc_dense):
        '''
        :param keypoint_scores: Bx1xN
        :param keypoint_desc: BxCxN
        :param scores_dense: Bx1xHxW
        :param desc_dense: BxCxHxW
        '''
        # TODO: loop if window_size is greater than 2 (for cycle loss)
        bsz, encoder_dim, n_points = keypoint_desc.size()
        batch_size = int(bsz / self.window_size)
        _, _, height, width = desc_dense.size()

        src_desc = keypoint_desc[::self.window_size] # B x C x N
        src_desc = F.normalize(src_desc, dim=1)

        tgt_desc_dense = desc_dense[1::self.window_size] # B x C x H x W
        tgt_desc_unrolled = F.normalize(tgt_desc_dense.view(batch_size, encoder_dim, -1), dim=1) # B x C x HW

        match_vals = torch.matmul(src_desc.transpose(2, 1).contiguous(), tgt_desc_unrolled) # B x N x HW
        soft_match_vals = F.softmax(match_vals / self.softmax_temp, dim=2)  # B x N x HW

        v_coord, u_coord = torch.meshgrid([torch.arange(0, height), torch.arange(0, width)])
        v_coord = v_coord.reshape(height * width).float()  # HW
        u_coord = u_coord.reshape(height * width).float()
        coords = torch.stack((u_coord, v_coord), dim=1)  # HW x 2
        tgt_coords_dense = coords.unsqueeze(0).expand(batch_size, height * width, 2).to(self.gpuid) # B x HW x 2

        pseudo_coords = torch.matmul(tgt_coords_dense.transpose(2, 1).contiguous(),
            soft_match_vals.transpose(2, 1).contiguous()).transpose(2, 1).contiguous()  # BxNx2

        # GET SCORES for pseudo point locations
        pseudo_norm = self.normalize_coords(pseudo_coords, height, width).unsqueeze(1)          # B x 1 x N x 2
        tgt_scores_dense = scores_dense[1::self.window_size]
        pseudo_scores = F.grid_sample(tgt_scores_dense, pseudo_norm, mode='bilinear')           # B x 1 x 1 x N
        pseudo_scores = pseudo_scores.reshape(batch_size, 1, n_points)                          # B x 1 x N
        # GET DESCRIPTORS for pseudo point locations
        pseudo_desc = F.grid_sample(tgt_desc_dense, pseudo_norm, mode='bilinear')               # B x C x 1 x N
        pseudo_desc = pseudo_desc.reshape(batch_size, encoder_dim, n_points)                    # B x C x N

        desc_match_score = torch.sum(src_desc * pseudo_desc, dim=1, keepdim=True) / float(encoder_dim) # Bx1xN
        src_scores = keypoint_scores[::self.window_size]
        match_weights = 0.5 * (desc_match_score + 1) * src_scores * pseudo_scores

        return pseudo_coords, match_weights

    def normalize_coords(self, coords_2D, width, height):
        # Normalizes coords_2D to be within [-1, 1]
        # coords_2D: B x N x 2
        batch_size = coords_2D.size(0)
        u_norm = (2 * coords_2D[:, :, 0].reshape(batch_size, -1) / (width - 1)) - 1
        v_norm = (2 * coords_2D[:, :, 1].reshape(batch_size, -1) / (height - 1)) - 1
        return torch.stack([u_norm, v_norm], dim=2)  # B x num_patches x 2
