import argparse
import sys
from copy import deepcopy
from datetime import datetime as dt, timedelta
from pathlib import Path

import pandas as pd
import pyedflib
from tqdm import tqdm

from eeglibrary.src import EEG

sys.path.append('..')
from src.const import CHBMIT_PATIENTS


SR = 256    # sample rate
DURATION_AS_ONE_ICTAL = 30
ICTAL_WINDOW_SIZE = 30


def annotate_args(parser):
    annotate_parser = parser.add_argument_group('annotation arguments')
    annotate_parser.add_argument('--n-jobs', type=int, default=4, help='Number of CPUs to use to annotate')
    annotate_parser.add_argument('--window_size', type=int, default=30, help='Split window size')
    annotate_parser.add_argument('--window_stride', type=int, default=15, help='Split window stride')
    annotate_parser.add_argument('--sop', type=int, default=30, help='Seizure onset time')
    annotate_parser.add_argument('--sph', type=int, default=5, help='Seizure prediction period')
    annotate_parser.add_argument('--interictal_hour', type=int, default=4, help='Hours before ictal time')

    return parser


class EDF:
    def __init__(self, file_path, start, end, n_seizures):
        self.file_path = file_path
        self.start = start
        self.end = end
        self.n_seizures = n_seizures
        self.interictal_time_list = []
        self.preictal_time_list = []
        self.ictal_time_list = []

    def add_ictal_section(self, start_sec, end_sec):
        # selfのstartとendから、ictalのstartとendを計算して格納する
        self.ictal_time_list.append({'start': self.start + timedelta(seconds=start_sec),
                                     'end': self.start + timedelta(seconds=end_sec)})

    def _time_to_index(self, section):
        start_index = int((section['start'] - self.start).total_seconds()) * SR
        end_index = int((section['end'] - self.start).total_seconds()) * SR
        return start_index, end_index

    def save_labels(self, save_dir, window_size, window_stride, n_jobs):
        saved_list = []
        for label in ['interictal', 'preictal', 'ictal']:
            if label == 'ictal':
                window_size = ICTAL_WINDOW_SIZE
                window_stride = ICTAL_WINDOW_SIZE

            time_list = getattr(self, f'{label}_time_list')
            try:
                eeg = load_edf(self.file_path)
            except OSError as e:
                print(e)
                return []

            for section in time_list:
                start_index, end_index = self._time_to_index(section)
                section_eeg = deepcopy(eeg)
                section_eeg.len_sec = (end_index - start_index) // SR
                section_eeg.values = section_eeg.values[:, start_index:end_index]
                saved_list.extend(section_eeg.split_and_save(window_size=window_size, n_jobs=n_jobs, padding=0,
                                                             window_stride=window_stride, save_dir=save_dir,
                                                             suffix=f'_{label}'))
        return saved_list


def load_edf(edf_path):
    edfreader = pyedflib.EdfReader(str(edf_path))
    return EEG.from_edf(edfreader)


def parse_after_24h(datetime_str):
    if int(datetime_str.split(':')[0]) >= 24:
        hour = int(datetime_str.split(':')[0]) - 24
        datetime_str = ':'.join([f'{hour:02}'] + datetime_str.split(':')[1:])

    return dt.strptime(datetime_str, '%H:%M:%S')


def modify_date(edf_list):
    days_passed = 0
    prev_edf = EDF(file_path='', start=dt.min, end=dt.min, n_seizures=0)
    for edf in edf_list:
        # そのedfファイル内で日付をまたいだ場合
        if edf.end.time() < edf.start.time():
            edf.start += timedelta(days=days_passed)
            days_passed += 1
            edf.end += timedelta(days=days_passed)

            for ictal_section in edf.ictal_time_list:   # このときが要注意。
                if ictal_section['end'] < ictal_section['start']:   # この場合ictalは日付をまたいでいる
                    ictal_section['start'] += timedelta(days=days_passed - 1)
                    ictal_section['end'] += timedelta(days=days_passed)
                elif 20 <= ictal_section['end'].hour < 24:      # ファイルは最大で4hなので、この場合ictalは前の日に起きている
                    ictal_section['start'] += timedelta(days=days_passed - 1)
                    ictal_section['end'] += timedelta(days=days_passed - 1)
                elif 0 <= ictal_section['end'].hour < 5:      # ファイルは最大で4hなので、この場合ictalは次の日に起きている
                    ictal_section['start'] += timedelta(days=days_passed)
                    ictal_section['end'] += timedelta(days=days_passed)

        # それ以外のときはstart, endともにdays_passed分を足す
        else:
            if edf.start.time() < prev_edf.end.time():  # 前のedfと今のedfで日付をまたいだ場合
                days_passed += 1
            edf.start += timedelta(days=days_passed)
            edf.end += timedelta(days=days_passed)

            for ictal_section in edf.ictal_time_list:
                for key in ictal_section.keys():
                    ictal_section[key] += timedelta(days=days_passed)

        prev_edf = edf
    return edf_list


def calc_allowed_interictal_time_list(edf_list, ictal_section_list, interictal_hour=4):
    allowed_interictal_time_list = []
    whole_edf_start = edf_list[0].start
    whole_edf_end = edf_list[-1].end

    for i, ictal_section in enumerate(ictal_section_list):
        prev_ictal_section = {'end': whole_edf_start - timedelta(hours=interictal_hour)} if i == 0 else ictal_section_list[i - 1]
        if ictal_section['start'] - prev_ictal_section['end'] > timedelta(hours=interictal_hour * 2):
            allowed_interictal_time_list.append({'start': prev_ictal_section['end'] + timedelta(hours=interictal_hour),
                                                 'end': ictal_section['start'] - timedelta(hours=interictal_hour)})

    if whole_edf_end - ictal_section_list[-1]['end'] > timedelta(hours=interictal_hour):
        allowed_interictal_time_list.append({'start': ictal_section_list[-1]['end'] + timedelta(hours=interictal_hour),
                                             'end': whole_edf_end})
    return allowed_interictal_time_list


def calc_allowed_preictal_time_list(edf_list, ictal_section_list, seizure_onset_period, seizure_prediction_horizon):
    allowed_preictal_time_list = []

    for i, ictal_section in enumerate(ictal_section_list):
        prev_ictal_section = {'end': edf_list[0].start} if i == 0 else ictal_section_list[i - 1]

        if ictal_section['start'] - prev_ictal_section['end'] > timedelta(minutes=DURATION_AS_ONE_ICTAL):
            duration = min((ictal_section['start'] - prev_ictal_section['end']).seconds, seizure_onset_period * 60)
            allowed_preictal_time_list.append({
                'start': ictal_section['start'] - timedelta(seconds=duration + seizure_prediction_horizon * 60),
                'end': ictal_section['start'] - timedelta(minutes=seizure_prediction_horizon)})
    return allowed_preictal_time_list


def annotate_chbmit(data_dir, annotate_conf):
    """
    CHB-MITデータ・セットのアノテーションを行う。
    使用するユーザデータからictalの時間を取り出し、その時間からinterictalとpreictalの時間を決定する。
    それぞれのラベルについて決定した時間に基づいて、各edfファイルを読み込んではラベル付,pkl保存を行う。
    最後にmanifestファイルを作成して終了。これを各患者毎に行う。

    SOP(seizure onset period)...この時間の範囲内で発作が起きるとする時間幅(分)。preictalの時間幅に対応する。
    SPH(seizure prediction horizon)...alertからSOPの開始までの時間幅(分)。preictalとictalの間の時間幅に対応する。
    preictalの時間はictal_start - (SOP + SPH) から ictal_start - SPH までである。
    """
    window_size = annotate_conf['window_size']  # second
    window_stride = annotate_conf['window_stride']  # second
    seizure_onset_period = annotate_conf['sop']  # minute
    seizure_prediction_horizon = annotate_conf['sph']  # minute
    interictal_hour = annotate_conf['interictal_hour']  # hour

    for patient_folder in Path(data_dir).iterdir():
        if not (patient_folder.is_dir() and patient_folder.name in CHBMIT_PATIENTS):
            continue

        print(patient_folder.name)

        edf_list = []
        ictal_section_list = []

        with open(str(patient_folder / f'{patient_folder.name}-summary.txt'), 'r') as f:
            summary = f.read().split('\n\n')[2:]

        edf_path_list = [str(path).replace('+', '__') for path in patient_folder.iterdir() if path.suffix == '.edf']
        edf_path_list.sort()
        edf_path_list = [Path(path.replace('__', '+')) for path in edf_path_list]

        for nth_edf in range(len(edf_path_list)):
            edf_info = summary[nth_edf].split('\n')
            file_path = patient_folder / edf_info[0].split(': ')[-1]
            start = parse_after_24h(edf_info[1].split(': ')[-1])
            end = parse_after_24h(edf_info[2].split(': ')[-1])
            n_seizures = int(edf_info[3].split(': ')[-1])
            edf = EDF(file_path, start, end, n_seizures)

            for nth_seizure in range(n_seizures):
                start_sec = int(
                    summary[nth_edf].split('\n')[4 + nth_seizure * 2].split(': ')[-1].replace(' ', '').replace(
                        'seconds', ''))
                assert start_sec != 0
                end_sec = int(
                    summary[nth_edf].split('\n')[5 + nth_seizure * 2].split(': ')[-1].replace(' ', '').replace(
                        'seconds', ''))
                edf.add_ictal_section(start_sec, end_sec)
                ictal_section_list.append(edf.ictal_time_list[-1])

            edf_list.append(edf)

        # 日付が順番に増えていくように変更
        edf_list = modify_date(edf_list)

        # interictalの区間を決定し、各edfインスタンスに格納していく
        allowed_inte = calc_allowed_interictal_time_list(edf_list, ictal_section_list, interictal_hour)
        pointer = 0
        for edf in edf_list:
            if edf.start > allowed_inte[pointer]['end']:
                pointer += 1

            if pointer >= len(allowed_inte):
                break

            start = edf.start if edf.start >= allowed_inte[pointer]['start'] else allowed_inte[pointer]['start']
            end = edf.end if edf.end <= allowed_inte[pointer]['end'] else allowed_inte[pointer]['end']

            if start < end:
                edf.interictal_time_list.append({'start': start, 'end': end})

        # preictalの区間を決定し、各edfインスタンスに格納していく
        allowed_pre = calc_allowed_preictal_time_list(edf_list, ictal_section_list, seizure_onset_period,
                                                      seizure_prediction_horizon)
        pointer = 0
        for edf in edf_list:
            if edf.start > allowed_pre[pointer]['end']:
                pointer += 1

            if pointer >= len(allowed_pre):
                break

            start = edf.start if edf.start >= allowed_pre[pointer]['start'] else allowed_pre[pointer]['start']
            end = edf.end if edf.end <= allowed_pre[pointer]['end'] else allowed_pre[pointer]['end']

            if start < end:
                edf.preictal_time_list.append({'start': start, 'end': end})

        # 各edfファイルについて、各ラベルの時間帯があればそれを保存する
        saved_path_list = []
        for edf in tqdm(edf_list, disable=True):
            dir_name = f'interictal_preictal_{seizure_onset_period}_{seizure_prediction_horizon}_{window_size}_{window_stride}'
            save_dir = (edf.file_path.parent / dir_name / edf.file_path.name[:-4]).resolve()
            save_dir.mkdir(exist_ok=True, parents=True)
            saved_path_list.extend(edf.save_labels(save_dir, window_size, window_stride, annotate_conf['n_jobs']))

        print(save_dir.parent.name)
        print(pd.Series(saved_path_list).apply(lambda x: x.split('/')[-1].replace('.pkl', '').split('_')[-1]).value_counts())
        pd.DataFrame(saved_path_list).to_csv(save_dir.parent / 'manifest.csv', header=None, index=False)
        # exit()


if __name__ == '__main__':
    data_dir = '/media/tomoya/SSD-PGU3/research/brain/chb-mit/'
    parser = argparse.ArgumentParser(description='Annotation arguments')
    annotate_conf = vars(annotate_args(parser).parse_args())
    annotate_chbmit(data_dir, annotate_conf)
