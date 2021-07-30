FROM python:3.8

# 해당 디렉토리에 있는 모든 하위항목들을 '/code`로 복사한다
COPY . /code

# image의 directory로 이동하고
WORKDIR /code

# 필요한 의존성 file들 설치
RUN apt update
RUN apt-get install -y python3-pyqt5 python3-pyqt5.qtmultimedia ffmpeg libpulse-mainloop-glib0
RUN pip install -r requirements.txt

# container가 구동되면 실행
# ENTRYPOINT ["python", "main.py"]
