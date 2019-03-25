from torch.utils.data import DataLoader
import torch
import numpy as np


class EEGDataLoader(DataLoader):
    def __init__(self, *args, **kwargs):
        super(EEGDataLoader, self).__init__(*args, **kwargs)
    #
    # def collate_fn(self, batch):


def make_weights_for_balanced_classes(labels, n_classes):
    labels = np.array(labels, dtype=int)
    class_count = [np.sum(labels == class_index) for class_index in range(n_classes)]
    weight_per_class = 1 / torch.Tensor(class_count)
    # weights = [weight_per_class[label] for label in labels]
    return weight_per_class.double()
