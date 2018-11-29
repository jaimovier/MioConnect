from src.public.myohw import *


class Config:

    MYO_AMOUNT = 2
    EMG_MODE = EmgMode.myohw_emg_mode_send_emg
    IMU_MODE = ImuMode.myohw_imu_mode_send_data
    CLASSIFIER_MODE = ClassifierMode.myohw_classifier_mode_disabled

    PRINT_EMG = False
    PRINT_IMU = False

    GET_MYO_INFO = True

    MESSAGE_DELAY = 0.1