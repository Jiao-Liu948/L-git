import os
import cv2
import csv
import hyperlpr3 as lpr
import tempfile
from flask import Flask, request, jsonify
from flask import send_from_directory
from flask_cors import CORS  
import numpy as np
from collections import Counter
import imghdr  # 用于验证图片真实类型
import threading
import time
import base64
from flask_socketio import SocketIO, emit
import logging
from  MySQLHelper import MySQLHelper
# 解决 KMP 库冲突
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

UPLOAD_FOLDER = "./uploads"
CSV_FILE_PATH = "./license_plates.csv"  # 车牌登记CSV表路径
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  

def connet_sql():
    dbconn = MySQLHelper("127.0.0.1", "root", "jzq983944", "plate_ocr", 3306)
    dbconn.getConnect()
    return dbconn
def inset_data(plate):
    dbconn = connet_sql()
    sql = "insert into plate_records(plate_num) values(%s)"
    dbconn.executeUpdateSQL(sql, [plate])
    dbconn.close()
    
'''# 初始化CSV文件
def init_csv():
    if not os.path.exists(CSV_FILE_PATH):
        with open(CSV_FILE_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['plate_num'])  # 表头：车牌
        logging.info(f"初始化CSV车牌表：{CSV_FILE_PATH}")

# 调用初始化函数
init_csv()'''

# 允许的文件格式
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv'}
ALLOWED_ALL_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS

# 全局变量
camera_thread = None
camera_running = False
camera_lock = threading.Lock()

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 初始化HyperLPR3识别器
try:
    catcher = lpr.LicensePlateCatcher()
    logger.info("HyperLPR3识别器初始化成功")
except Exception as e:
    logger.error(f"HyperLPR3识别器初始化失败: {e}")
    catcher = None

def get_registered_plates(plate):
    try: 
        sql = f"SELECT COUNT(*) FROM plate_records WHERE plate_num = '{plate}'"
        dbconn = connet_sql()
        count = dbconn.executeQuery(sql)
    except Exception as e:
        logger.error(f"读取数据库失败：{str(e)}")
    return count

# 2. 检查车牌是否已登记
def is_plate_registered(plate):
    if not plate or plate in ["未检测到车牌", "车牌识别失败", "识别错误"]:
        return False, "无效车牌"
    count = get_registered_plates(plate)[0][0]
    print(count)
    if not count:  # 如果查询结果为空或计数为0
        count = 0
    return count > 0, "已登记" if count > 0 else "未登记"

# 3. 将车牌写入数据库（确认入库）
def register_plate(plate):
    if not plate or plate in ["未检测到车牌", "车牌识别失败", "识别错误"]:
        return False, "无效车牌，无法入库"
    # 先检查是否已存在
    is_registered, _ = is_plate_registered(plate)
    if is_registered:
        return False, f"车牌{plate}已登记，无需重复入库"
    # 写入数据库
    try:
        inset_data(plate)
        logger.info(f"车牌{plate}已成功入库")
        return True, f"车牌{plate}入库成功"
    except Exception as e:
        logger.error(f"写入数据库失败：{str(e)}")
        return False, f"入库失败：{str(e)}"

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_ALL_EXTENSIONS

# 2. 判断文件真实类型（避免后缀伪造）
def get_file_type(file_bytes, filename):
    # 先验证图片类型
    try:
        img_type = imghdr.what(None, h=file_bytes)
        if img_type in ALLOWED_IMAGE_EXTENSIONS:
            return "image"
    except:
        pass
    # 验证视频类型
    ext = filename.rsplit('.', 1)[1].lower()
    if ext in ALLOWED_VIDEO_EXTENSIONS:
        return "video"
    return "unknown"

#单帧车牌识别
def recognize_single_frame(frame):
    try:
        if catcher is None:
            return frame, "识别器未初始化"
        
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        results = catcher(frame_rgb)
        
        if len(results) == 0:
            return frame, "未检测到车牌"
        
        #获取最可信的结果
        plate_text = results[0][0]
        
        #在图像上标注结果
        if len(results[0]) > 1 and isinstance(results[0][1], (list, tuple)) and len(results[0][1]) == 4:
            # 如果有边界框信息，绘制边界框
            x1, y1, x2, y2 = results[0][1]
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
            cv2.putText(frame, plate_text, (int(x1), int(y1)-10), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        
        return frame, plate_text
            
    except Exception as e:
        logger.error(f"帧识别错误: {str(e)}")
        return frame, f"识别错误: {str(e)}"

# 4. 摄像头线程函数
def camera_stream():
    global camera_running
    cap = None
    
    try:
        cap = cv2.VideoCapture(0)  # 使用默认摄像头
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 15)  # 降低帧率，减少CPU使用
        
        if not cap.isOpened():
            socketio.emit('camera_error', {'msg': '无法打开摄像头'})
            return
        
        logger.info("摄像头已开启")
        socketio.emit('camera_status', {'status': 'started', 'msg': '摄像头已开启'})
        
        frame_count = 0
        last_result = "未识别"
        last_register_status = "未登记"
        
        while camera_running:
            ret, frame = cap.read()
            if not ret:
                socketio.emit('camera_error', {'msg': '摄像头读取失败'})
                break
            
            # 每3帧识别一次（降低CPU负载）
            if frame_count % 3 == 0:
                processed_frame, result = recognize_single_frame(frame)
                if result != "未检测到车牌" and not result.startswith("识别错误"):
                    last_result = result
                    # 检查登记状态
                    _, last_register_status = is_plate_registered(result)
            else:
                processed_frame = frame
                result = last_result
            
            # 压缩图像为JPEG并转为base64
            _, buffer = cv2.imencode('.jpg', processed_frame, 
                                     [cv2.IMWRITE_JPEG_QUALITY, 70])
            frame_base64 = base64.b64encode(buffer).decode('utf-8')
            
            # 发送帧、识别结果、登记状态
            socketio.emit('camera_frame', {
                'frame': frame_base64,
                'result': last_result,
                'register_status': last_register_status
            })
            
            frame_count += 1
            time.sleep(0.05)  # 控制帧率
            
    except Exception as e:
        logger.error(f"摄像头线程错误: {str(e)}")
        socketio.emit('camera_error', {'msg': f'摄像头错误: {str(e)}'})
        
    finally:
        if cap:
            cap.release()
        camera_running = False
        socketio.emit('camera_status', {'status': 'stopped', 'msg': '摄像头已关闭'})
        logger.info("摄像头已关闭")

@socketio.on('start_camera')
def handle_start_camera():
    global camera_thread, camera_running
    
    with camera_lock:
        if not camera_running:
            camera_running = True
            camera_thread = threading.Thread(target=camera_stream)
            camera_thread.daemon = True
            camera_thread.start()
            logger.info("启动摄像头线程")

@socketio.on('stop_camera')
def handle_stop_camera():
    global camera_running
    
    with camera_lock:
        camera_running = False
        logger.info("停止摄像头请求已接收")

def recognize_image(img):
    try:
        if catcher is None:
            return "识别器未初始化"
        
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        #使用HyperLPR3识别
        results = catcher(img_rgb)
        
        if len(results) == 0:
            return "未检测到车牌"
        
        return results[0][0]  # 返回最可信的结果
    except Exception as e:
        logger.error(f"图片识别失败：{str(e)}")
        return f"图片识别失败：{str(e)}"

#视频车牌识别
def recognize_video(video_path):
    plate_results = []
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return "视频文件无法打开"
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_interval = 5  # 每5帧识别一次，提升效率
        current_frame = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            if current_frame % frame_interval == 0:
                plate = recognize_image(frame)  # 直接调用图片识别函数
                if plate not in ["未检测到车牌", "识别器未初始化"] and not plate.startswith("图片识别失败"):
                    plate_results.append(plate)
            
            current_frame += 1

        # 取出现次数最多的车牌
        if plate_results:
            return Counter(plate_results).most_common(1)[0][0]
        else:
            return "视频中未识别到任何车牌"
    except Exception as e:
        logger.error(f"视频识别失败：{str(e)}")
        return f"视频识别失败：{str(e)}"
    finally:
        if cap:
            cap.release()

# 前端页面
@app.route('/')
def index():
    return send_from_directory('.', 'recognition.html')

#统一识别接口
@app.route('/api/recognize_all', methods=['POST'])
def api_recognize_all():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 0, "msg": "请上传图片或视频文件"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"code": 0, "msg": "文件名为空"}), 400
        
        if not allowed_file(file.filename):
            return jsonify({
                "code": 0, 
                "msg": f"仅支持格式：{','.join(ALLOWED_ALL_EXTENSIONS)}"
            }), 400
        
        file_bytes = file.read()
        file_type = get_file_type(file_bytes, file.filename)
        if file_type == "unknown":
            return jsonify({"code": 0, "msg": "无法识别文件类型，请确认是图片/视频"}), 400
        
        result = ""
        if file_type == "image":
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            result = recognize_image(img)
        else:
            temp_fd, temp_path = tempfile.mkstemp(suffix='.' + file.filename.rsplit('.', 1)[1].lower())
            os.close(temp_fd)
            file.seek(0)  # 重置文件指针
            file.save(temp_path)
            result = recognize_video(temp_path)
            os.remove(temp_path)  # 删除临时文件
        
        # 检查登记状态
        _, register_status = is_plate_registered(result)
        
        return jsonify({
            "code": 1, 
            "msg": f"{file_type}识别成功", 
            "file_type": file_type,
            "result": result,
            "register_status": register_status  # 新增登记状态返回
        })
    
    except Exception as e:
        logger.error(f"识别接口错误: {str(e)}")
        return jsonify({"code": 0, "msg": f"识别失败：{str(e)}"}), 500

# 确认入库接口
@app.route('/api/register_plate', methods=['POST'])
def api_register_plate():
    try:
        data = request.get_json()
        plate = data.get('plate', '').strip()
        if not plate:
            return jsonify({"code": 0, "msg": "车牌不能为空"}), 400
        
        success, msg = register_plate(plate)
        return jsonify({
            "code": 1 if success else 0,
            "msg": msg
        })
    except Exception as e:
        return jsonify({"code": 0, "msg": f"入库失败：{str(e)}"}), 500

# 开启闸口接口（空接口，可后续扩展）
@app.route('/api/open_gate', methods=['POST'])
def api_open_gate():
    try:
        data = request.get_json()
        plate = data.get('plate', '').strip()
        
        is_reg, status = is_plate_registered(plate)
        if is_reg:
            return jsonify({"code": 1, "msg": f"车牌{plate}已登记，闸口开启成功"})
        else:
            return jsonify({"code": 0, "msg": f"车牌{plate}未登记，禁止开启闸口"})
    except Exception as e:
        return jsonify({"code": 0, "msg": f"闸口操作失败：{str(e)}"}), 500

# 调试接口
@app.route('/api/status', methods=['GET'])
def api_status():
    status = {
        'hyperlpr3_initialized': catcher is not None,
        'csv_file_exists': os.path.exists(CSV_FILE_PATH),
        'registered_plates_count': len(get_registered_plates())
    }
    return jsonify({"code": 1, "status": status})

# 启动服务
if __name__ == '__main__':
    logger.info("车牌识别服务启动中...")
    logger.info("访问地址: http://127.0.0.1:5000")
    # logger.info("CSV文件路径: " + CSV_FILE_PATH)
    
    # 检查HyperLPR3状态
    if catcher is None:
        logger.warning("HyperLPR3识别器初始化失败，请检查依赖安装")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)