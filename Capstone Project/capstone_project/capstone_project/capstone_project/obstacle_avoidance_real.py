#!/usr/bin/env python3

# Copyright (c) 2023, Tinker Twins
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# ROS2 module imports
import rclpy # ROS2 client library (rcl) for Python (built on rcl C API)
from rclpy.node import Node # Node class for Python nodes
from geometry_msgs.msg import Twist # Twist (linear and angular velocities) message class
from sensor_msgs.msg import LaserScan # LaserScan (LIDAR range measurements) message class
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy # Ouality of Service (tune communication between nodes)
from rclpy.qos import qos_profile_sensor_data # Ouality of Service for sensor data, using best effort reliability and small queue depth
from rclpy.duration import Duration # Time duration class

# Python mudule imports
import numpy as np # Numpy
import queue # FIFO queue
import time # Tracking time

# PID controller class
class PIDController:
    '''
    Generates control action taking into account instantaneous error (proportional action),
    accumulated error (integral action) and rate of change of error (derivative action).
    '''
    def __init__(self, kP, kI, kD, kS):
        self.kP       = kP # Proportional gain
        self.kI       = kI # Integral gain
        self.kD       = kD # Derivative gain
        self.kS       = kS # Saturation constant (error history buffer size)
        self.err_int  = 0 # Error integral
        self.err_dif  = 0 # Error difference
        self.err_prev = 0 # Previous error
        self.err_hist = queue.Queue(self.kS) # Limited buffer of error history
        self.t_prev   = 0 # Previous time

    def control(self, err, t):
        '''
        Generate PID controller output.
        :param err: Instantaneous error in control variable w.r.t. setpoint
        :param t  : Current timestamp
        :return u: PID controller output
        '''
        dt = t - self.t_prev # Timestep
        if dt > 0.0:
            self.err_hist.put(err) # Update error history
            self.err_int += err # Integrate error
            if self.err_hist.full(): # Jacketing logic to prevent integral windup
                self.err_int -= self.err_hist.get() # Rolling FIFO buffer
            self.err_dif = (err - self.err_prev) # Error difference
            u = (self.kP * err) + (self.kI * self.err_int * dt) + (self.kD * self.err_dif / dt) # PID control law
            self.err_prev = err # Update previos error term
            self.t_prev = t # Update timestamp
            return u # Control signal

# Node class
class RobotController(Node):

    #######################
    '''Class constructor'''
    #######################

    def __init__(self):
        # Information and debugging
        info = '\nMake the robot avoid obstacles by maintaining a safe distance from them.\n'
        print(info)
        # ROS2 infrastructure
        super().__init__('robot_controller') # Create a node with name 'robot_controller'
        qos_profile = QoSProfile( # Ouality of Service profile
        reliability=QoSReliabilityPolicy.RMW_QOS_POLICY_RELIABILITY_RELIABLE, # Reliable (not best effort) communication
        history=QoSHistoryPolicy.RMW_QOS_POLICY_HISTORY_KEEP_LAST, # Keep/store only up to last N samples
        depth=10 # Queue size/depth of 10 (only honored if the “history” policy was set to “keep last”)
        )
        self.robot_scan_sub = self.create_subscription(LaserScan, '/scan', self.robot_laserscan_callback, qos_profile_sensor_data) # Subscriber which will subscribe to LaserScan message on the topic '/scan' adhering to 'qos_profile_sensor_data' QoS profile
        self.robot_scan_sub # Prevent unused variable warning
        self.robot_ctrl_pub = self.create_publisher(Twist, '/cmd_vel', qos_profile) # Publisher which will publish Twist message to the topic '/cmd_vel' adhering to 'qos_profile' QoS profile
        timer_period = 0.001 # Node execution time period (seconds)
        self.timer = self.create_timer(timer_period, self.robot_controller_callback) # Define timer to execute 'robot_controller_callback()' every 'timer_period' seconds
        self.laserscan = None # Initialize variable to capture the laserscan
        self.ctrl_msg = Twist() # Robot control commands (twist)
        self.start_time = self.get_clock().now() # Record current time in seconds
        self.pid_lat = PIDController(0.22, 0.01, 0.3, 10) # Lateral PID controller object initialized with kP, kI, kD, kS
        self.pid_lon = PIDController(0.11, 0.001, 0.01, 10) # Longitudinal PID controller object initialized with kP, kI, kD, kS
        self.data_available = False # Initialize data available flag to false
        
    ########################
    '''Callback functions'''
    ########################

    def robot_laserscan_callback(self, msg):
        self.laserscan = np.asarray(msg.ranges) # Capture most recent laserscan
        self.laserscan[self.laserscan >= 3.5] = 3.5 # Filter laserscan data based on maximum range
        self.data_available = True # Set data available flag to true

    def robot_controller_callback(self):
        DELAY = 4.0 # Time delay (s)
        if self.get_clock().now() - self.start_time > Duration(seconds=DELAY):
            if self.data_available:
                # Front sector ranging
                front_sector = 20 # Angular range (deg)
                front = np.mean(self.laserscan[0:front_sector])+np.mean(self.laserscan[360-front_sector:360])/2 # Frontal distance to collision (DTC)
                # Oblique sector ranging
                oblique_sector = 70 # Angular range (deg)
                oblique_left = np.mean( self.laserscan[0:oblique_sector]) # Oblique left DTC
                oblique_right = np.mean( self.laserscan[360-oblique_sector:360])  # Oblique right DTC
                # Side sector ranging
                side_sector = 55 # Angular range (deg)
                left = np.mean( self.laserscan[30:30+side_sector]) # Left DTC
                right = np.mean( self.laserscan[330-side_sector:330]) # Right DTC
                # Control logic
                tstamp = time.time() # Current timestamp (s)
                if oblique_left < 0.5 or oblique_right < 0.5: # Too close to obstacle(s)
                    LIN_VEL = 0.005 # Linear velocity (m/s)
                    ANG_VEL = self.pid_lat.control(16*(left-right), tstamp) # Angular velocity (rad/s) from PID controller
                elif (oblique_left > 0.5 and oblique_left < 1) or (oblique_right > 0.5 and oblique_right < 1): # Fairly away from obstacles
                    LIN_VEL = self.pid_lon.control(front, tstamp) # Linear velocity (m/s) from PID controller
                    ANG_VEL = self.pid_lat.control(left-right, tstamp) # Angular velocity (rad/s) from PID controller
                else: # Safely away from obstacles
                    LIN_VEL = 0.2 # Linear velocity (m/s)
                    ANG_VEL = self.pid_lat.control(left-right, tstamp) # Angular velocity (rad/s) from PID controller
                self.ctrl_msg.linear.x = min(0.22, LIN_VEL) # Set linear velocity
                self.ctrl_msg.angular.z = min(2.84, ANG_VEL) # Set angular velocity
                self.robot_ctrl_pub.publish(self.ctrl_msg) # Publish robot controls message
                print('Distance to closest obstacle is {} m'.format(round(min(self.laserscan), 4)))
                #print('Robot moving with {} m/s and {} rad/s'.format(LIN_VEL, ANG_VEL))
        else:
            print('Initializing...')

def main(args=None):
    rclpy.init(args=args) # Start ROS2 communications
    node = RobotController() # Create node
    rclpy.spin(node) # Execute node
    node.destroy_node() # Destroy node explicitly (optional - otherwise it will be done automatically when garbage collector destroys the node object)
    rclpy.shutdown() # Shutdown ROS2 communications

if __name__ == "__main__":
    main()
