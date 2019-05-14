from __future__ import print_function, division

from eeglibrary.src.eeg_loader import from_mat
import pandas as pd

from torch.utils.data.sampler import WeightedRandomSampler

from eeglibrary.src import EEGDataSet, EEGDataLoader, make_weights_for_balanced_classes, EEG
from eeglibrary.models.CNN import *
from eeglibrary.models.RNN import *
from eeglibrary.models.toolbox import *


def common_eeg_setup(eeg_path='', mat_col=''):
    eeg_conf = dict(spect=True,
                    window_size=1.0,
                    window_stride=1.0,
                    window='hamming',
                    sample_rate=1500,
                    noise_dir=None,
                    noise_prob=0.4,
                    noise_levels=(0.0, 0.5))
    eeg_path = eeg_path or '/home/tomoya/workspace/kaggle/seizure-prediction/input/Dog_1/train/Dog_1_interictal_segment_0001.mat'
    mat_col = mat_col or 'interictal_segment_1'
    return from_mat(eeg_path, mat_col), eeg_conf


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def init_seed(args):
    # Set seeds for determinism
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)


def init_device(args):
    device = torch.device("cuda" if args.cuda else "cpu")
    if args.cuda:
        torch.cuda.set_device(args.gpu_id)
    return device


def set_eeg_conf(args):
    manifest_path = [value for key, value in vars(args).items() if 'manifest' in key][0]
    one_eeg_path = pd.read_csv(manifest_path).values[0][0]
    n_elect = len(EEG.load_pkl(one_eeg_path).channel_list)
    eeg_conf = dict(spect=args.spect,
                    n_elect=n_elect,
                    duration=args.duration,
                    window_size=args.window_size,
                    window_stride=args.window_stride,
                    window='hamming',
                    sample_rate=args.sample_rate,
                    low_cutoff=args.l_cutoff,
                    high_cutoff=args.h_cutoff)
    return eeg_conf


def set_dataloader(args, class_names, label_func, eeg_conf, phase, device='cpu'):
    if phase == 'test':
        dataset = EEGDataSet(args.test_manifest, eeg_conf, label_func, args.to_1d, classes=None, return_path=True)
        dataloader = EEGDataLoader(dataset, batch_size=args.batch_size, num_workers=args.num_workers,
                                          pin_memory=True, shuffle=False)
    else:
        manifest_path = [value for key, value in vars(args).items() if phase in key][0]
        dataset = EEGDataSet(manifest_path, eeg_conf, label_func, args.to_1d, class_names, device=device)
        weights = make_weights_for_balanced_classes(dataset.labels_index(), len(class_names))
        sampler = WeightedRandomSampler(weights, int(len(dataset) * args.epoch_rate))
        dataloader = EEGDataLoader(dataset, batch_size=args.batch_size, num_workers=args.num_workers,
                                          pin_memory=True, sampler=sampler, drop_last=True)
    return dataloader


def set_model(args, class_names, eeg_conf, device):
    if args.model_name == 'cnn_1_16_399':
        model = cnn_1_16_399(eeg_conf, n_labels=len(class_names))
    elif args.model_name == 'cnn_16_751_751':
        model = cnn_16_751_751(eeg_conf, n_labels=len(class_names))
    elif args.model_name == 'rnn_16_751_751':
        cnn, out_ftrs = cnn_ftrs_16_751_751(eeg_conf)
        model = RNN(cnn, out_ftrs, args.batch_size, args.rnn_type, class_names, eeg_conf=eeg_conf)
    elif args.model_name == 'cnn_1_16_751_751':
        model = cnn_1_16_751_751(eeg_conf, n_labels=len(class_names))
    elif args.model_name == 'xgboost':
        model = XGBoost(list(range(len(class_names))))
    elif args.model_name == 'sgdc':
        model = SGDC(list(range(len(class_names))))
    elif args.model_name in ['kneighbor', 'knn']:
        args.model_name = 'kneighbor'
        model = KNN(list(range(len(class_names))))
    else:
        raise NotImplementedError

    if 'nn' in args.model_name:
        model = model.to(device)
        print(model)

    return model


def arrange_paths(args, sub_name):
    args.train_manifest = 'splitted/{}/train_manifest.csv'.format(sub_name)
    args.val_manifest = 'splitted/{}/val_manifest.csv'.format(sub_name)
    args.test_manifest = 'splitted/{}/test_manifest.csv'.format(sub_name)
    args.model_path = 'model/' + args.model_name + '/{}.pkl'.format(sub_name)
    args.log_id = sub_name

    return args