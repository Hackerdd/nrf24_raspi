#encoding=utf-8
#author:oxa71a5
#date:2017/11/07
import time
import os
import lxc_nrf24
from iot_devices.DHT_Sensor             import *
from iot_devices.AirconditionController import *
from iot_devices.ElectricIron           import *
from collections import defaultdict
import Queue
import json
import tornado.httpserver
import tornado.web
import tornado.ioloop
import threading
import logging
import sys
reload(sys)
sys.setdefaultencoding("utf8")

class IOT(threading.Thread):

    def __init__(self):
        self.__init__("mac00") #默认地址是mac00

    def __init__(self,my_addr):
        super(IOT,self).__init__()
        self.max_timeout = 10 #最多十次重发
        self.rwlock = threading.Lock() #获取读写锁
        self.rx_queue = {} #弃用defaultdict(Queue.Queue) #接收数据缓冲区 发送方地址作为索引 以默认队列形式存储收到的数据
        self.rx_queue_buffersize = 1000 #设定每个接受地址的最大缓冲是1000
        self.rx_addr_prefix = "mac0" #接收地址的前4位相同
        self.machine = lxc_nrf24.nrf24(my_addr = my_addr,channel = 12)
        self.machine.begin()
        self.flushHardwareRxBuffer()
        #while( self.machine.available() ): recv = self.machine.read_str() #Flush RX buffer

    def run(self): #多线程的常驻函数
        logging.info("IOT server listening...")
        self.listen()

    def listen(self): #监听无线信道
        sender_addr = "" #
        while True:
            __released = False #局部判断是否释放了锁 默认未曾释放
            self.rwlock.acquire()
            if(self.machine.available()):
                data = self.machine.read_str()
                try:
                    sender_addr = self.rx_addr_prefix + data[0] #获取接受方的地址
                    sender_payload = json.loads("{"+",".join(["\"{}\":\"{}\"".format(x.split(":")[0],x.split(":")[1]) for x in data.split(",")[1:]])+"}")
                    try:
                        self.rx_queue[sender_addr].put_nowait(sender_payload) #将接受到的数据放入到缓存队列中
                    except KeyError: #键值不存在
                        logging.debug("Allocate new buffer for new address '{}'".format(sender_addr))
                        self.rx_queue[sender_addr] = Queue.Queue(self.rx_queue_buffersize)
                        self.rx_queue[sender_addr].put_nowait(sender_payload) #将接受到的数据放入到缓存队列中
                    except Queue.Full: #缓冲满
                        logging.debug("Rx buffer full,rx addr '{}'".format(sender_addr))
                        #直接丢弃...
                except Exception as e: #有可能收到错误的数据包?
                    print e    
            else:
                self.rwlock.release() #释放读写锁
                time.sleep(0.05) #给其他操作空出获取锁的时间
                continue #避免二次释放读写锁
            self.rwlock.release() #释放读写锁

    def flushHardwareRxBuffer(self): #刷新硬件接收寄存器缓存
        self.rwlock.acquire()
        while( self.machine.available() ): recv = self.machine.read_str() #Flush RX buffer
        self.rwlock.release()

    def flushRxBuffer(self,key): #刷新本地内存接收队列缓存
        try:
            while(self.rx_queue[key].qsize()!=0): #将接收缓冲区对应接收地址下的所有数据都取出
                self.rx_queue[key].get()
        except Exception as e:
            logging.warning(str(e))

    def communicateToNode(self,machineId,typeVal,contentVal): #与指定节点进行通信 request&response模式
        toSendPacket = "{},{}:{}".format(machineId,typeVal,contentVal)
        returnTest = False
        returnResult = None
        try:
            if(machineId in self.rx_queue): #首先判断当前接收缓冲区是否有该id
                self.flushRxBuffer(machineId) #一次性清空之前接受到的数据 只是为了在本次发射之后收到数据  这种方式适用于 req&rep模式
            else:
                self.rx_queue[machineId] = Queue.Queue(self.rx_queue_buffersize) #开辟缓冲区
            check_point = time.time() #记录出发时间
            timeout = 0 
            while self.rx_queue[machineId].qsize() == 0 and timeout < self.max_timeout: #一直发送到接受数据 或者 超时
                self.rwlock.acquire() #获取读写锁
                self.machine.send_to(toSendPacket,machineId) #发送数据给对应节点
                self.rwlock.release() #释放读写锁
                check_point = time.time() #记录发射时间点
                while self.rx_queue[machineId].qsize() == 0 :
                    if(time.time() - check_point > 0.01): #20ms一次发射周期
                        timeout += 1
                        break
            if( self.rx_queue[machineId].qsize()!=0): #接受到了返回的数据
                returnResult = self.rx_queue[machineId].get()
                returnTest = True
            else:
                logging.warning("Communicate to node '{}' failed".format(machineId))
        except:
            pass
        return (returnTest,returnResult,)

    def communicateToNode_(self,machineId,typeVal,contentVal): #与指定节点进行通信(not good)
        toSendPacket = "{},{}:{}".format(machineId,typeVal,contentVal)
        #self.flushRxBuffer()
        timeout = 0
        while( self.machine.available() == False and timeout< self.max_timeout ):
            self.machine.send_to(toSendPacket,machineId)
            check_point = time.time() #记录发射时间点
            while( self.machine.available()==False ):
                if(time.time() - check_point > 0.01): #10ms试一次超时周期
                    timeout+=1 
                    break #超时一次
        if( self.machine.available() ):
            data = self.machine.read_str()
            sender_addr = data[0] #获取接受方的地址
            sender_payload = json.loads("{"+",".join(["\"{}\":\"{}\"".format(x.split(":")[0],x.split(":")[1]) for x in data.split(",")[1:]])+"}")
            if(sender_addr == machineId[-1]): #接收方地址与实际发送地址相符合
                return (True,sender_payload,)
        print "Communicate to node failed!"
        return (False,None)

class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.render("index.html")

    def post(self):
        self.write("")

class ApiHandler(tornado.web.RequestHandler):
    def get(self):
        self.post()

    def post(self):
        global dhtSensors
        global airCondition1

        postdata = self.request.body.decode('utf-8')
        postdata = json.loads(postdata)
        typeVal  = postdata["Type"]
        contentVal = postdata["Content"]
        returnData = {
            "Type":"",
            "Content":{
            }
        }
        deviceId = 0 #目标设备ID
        #deviceId = deviceId if(deviceId< len(dhtSensors)) else len(dhtSensors)-1 #判断设备ID是否合法
        if(typeVal == "getDhtStatus"):
            status = dhtSensors[deviceId].getOnlineStatus()
            returnData["Type"] = "getDhtStatusResult"
            returnData["Content"]["status"] = status
        elif( typeVal == "getTemperature" ):
            temperature = dhtSensors[deviceId].getTemperature()
            returnData["Type"] = "getTemperatureResult"
            returnData["Content"]["temperature"] = temperature
        elif( typeVal == "getHumidity" ):
            humidity = dhtSensors[deviceId].getHumidity()
            returnData["Type"] = "getHumidityResult"
            returnData["Content"]["humidity"] = humidity
        # 下面是空调控制器
        elif(typeVal == "getAcStatus"):
            status = airCondition1.getOnlineStatus()
            returnData["Type"] = "getAcStatusResult"
            returnData["Content"]["status"] = status
        elif(typeVal == "turnOnAc"):
            status = airCondition1.turnOnAc()
            returnData["Type"] = "turnOnAcResult"
            returnData["Content"]["result"] = status
        elif(typeVal == "turnOffAc"):
            status = airCondition1.turnOffAc()
            returnData["Type"] = "turnOffAcResult"
            returnData["Content"]["result"] = status
        elif(typeVal == "setAcTemperature"):
            temperature = contentVal["temperature"]
            status = airCondition1.setAcTemperature(temperature)
            returnData["Type"] = "setAcTemperatureResult"
            returnData["Content"]["result"] = status
        #下面是电烙铁控制器
        elif(typeVal == "getElectricIronStatus"):
            status = electricIron1.getOnlineStatus()
            returnData["Type"] = "getElectricIronStatusResult"
            returnData["Content"]["status"] = status
        elif(typeVal == "getElectricIronSwitchStatus"):
            status = electricIron1.getSwitchStatus()
            returnData["Type"] = "getElectricIronSwitchStatusResult"
            returnData["Content"]["status"] = status
        elif(typeVal == "turnOnElectricPower"):
            status = electricIron1.turnOnPower()
            returnData["Type"] = "turnOnElectricPowerResult"
            returnData["Content"]["result"] = status
        elif(typeVal == "turnOffElectricPower"):
            status = electricIron1.turnOffPower()
            returnData["Type"] = "turnOffElectricPowerResult"
            returnData["Content"]["result"] = status
        self.write(json.dumps(returnData))


if __name__ == '__main__':
    global myIOT
    global dhtSensors
    global airCondition1
    logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s [%(levelname)s] %(filename)s[%(lineno)d] %(message)s",
                    datefmt='%Y/%m/%d %H:%M:%S',
                    #filename='_server_.log',filemode='a'
                    )# debug info warning error cirtical
    myIOT = IOT("mac00") #创建Nrf服务器
    myIOT.setDaemon(True) #主线程退出后 该线程也会退出
    myIOT.start() #开始监听
    dhtSensor1 = DHT_Sensor( IOT_Center = myIOT,machineId = "mac01" )
    airCondition1 = AirconditionController( IOT_Center = myIOT,machineId = "mac02" )
    electricIron1 = ElectricIron( IOT_Center = myIOT,machineId = "mac03" )
    dhtSensors = [dhtSensor1,]


    app = tornado.web.Application([
        ('/', IndexHandler),
        ('/api', ApiHandler)
        ],cookie_secret='abcdswweww12314eqwdsgerhtrhw2!!wsws2',
        template_path=os.path.join(os.path.dirname(__file__), "."),
    )
    print "Running..."
    app.listen(80)
    tornado.ioloop.IOLoop.instance().start()