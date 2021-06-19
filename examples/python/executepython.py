#!/usr/bin/env python3

from moveit2 import MoveIt2Interface
import rclpy
import time
import threading


def main(args=None):
    rclpy.init(args=args)

    # Initialise MoveIt2
    moveit2 = MoveIt2Interface()
    # Spin MoveIt2 node in the background
    executor = rclpy.executors.MultiThreadedExecutor(1)
    executor.add_node(moveit2)
    thread = threading.Thread(target=executor.spin)
    thread.start()

    time.sleep(5)
    
    moveit2.log("starting planning")
    # Set pose goal to reach
    position = [0.8, 0, 0.1]
    quaternion = [1.0, 0.0, 0.0, 0.0]
    moveit2.set_pose_goal(position, quaternion)

    # Plan and execute
    moveit2.plan_kinematic_path()
    moveit2.log("planning done, starting execution")
    moveit2.execute()
    moveit2.log("execution")
    moveit2.wait_until_executed()
    moveit2.log("executed")
    time.sleep(10);

    # Set pose goal to reach
    position = [0.5, 0.5, 0.5]
    quaternion = [1.0, 0.0, 0.0, 0.0]
    moveit2.set_pose_goal(position, quaternion)

    # Plan and execute
    moveit2.plan_kinematic_path()
    moveit2.execute()
    moveit2.log("execution")
    moveit2.wait_until_executed()
    moveit2.log("executed")

    rclpy.shutdown()


if __name__ == "__main__":
    main()
