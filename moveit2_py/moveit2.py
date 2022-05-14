"""
A simple python interface with MoveIt2 services (and actions). This is an alternative to
moveit_commander, which is not yet ported to ROS 2 (as of Oct 2020).

Note: This module is currently configured for Franka Emika Panda robot and testing support for UR5 with RG2.
Note: There is no Ignition-specific code in this module (this repo is just a convinient place).
"""

import threading
from typing import Optional, Sequence

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.parameter import Parameter

from std_msgs.msg import Float32
from builtin_interfaces.msg import Duration
from geometry_msgs.msg import Pose, Quaternion
from shape_msgs.msg import SolidPrimitive
from sensor_msgs.msg import JointState
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from moveit_msgs.msg import Constraints, JointConstraint, PositionConstraint, OrientationConstraint, TrajectoryConstraints,PositionIKRequest, RobotTrajectory, MotionSequenceItem, MotionSequenceRequest, MotionPlanRequest, RobotState, PlanningOptions
from moveit_msgs.srv import GetPositionIK, GetPositionFK, GetMotionPlan, GetCartesianPath
from moveit_msgs.action import MoveGroup, ExecuteTrajectory, HybridPlanner, MoveGroupSequence
from action_msgs.msg import GoalStatus
import math
from control_msgs.msg import JointJog
class MoveIt2Interface(Node):

    def __init__(self, separate_gripper_controller: bool = False, use_sim_time: bool = True, node_name: str = 'ign_moveit2_py'):
        """
        robot_model - 'panda' and 'ur5_rg2' are supported
        """
        super().__init__(node_name)
        self.declare_parameter('robot_model', 'tm12')
        robot_model = self.get_parameter('robot_model').get_parameter_value().string_value
        self.set_parameters([Parameter('use_sim_time',
                                       type_=Parameter.Type.BOOL,
                                       value=use_sim_time)])

        self.init_robot(robot_model=robot_model,
                        separate_gripper_controller=separate_gripper_controller)
        self.init_compute_fk()
        self.init_compute_ik()
        self.init_plan_kinematic_path()
        self.init_plan_pilz_path()
        self.init_plan_cartesian_path()
        self.init_gripper()
        self.init_execute_trajectory()

        # Hybrid planning
        self.hp_action_client = ActionClient(self, HybridPlanner, '/run_hybrid_planning')
        self.hp_action_client.wait_for_server(timeout_sec=2.0)
        # pilz_industrial_motion_planner
        self.movegroup_action_client = ActionClient(self, MoveGroupSequence, '/sequence_move_group')
        self.movegroup_action_client.wait_for_server(timeout_sec=2.0)
        self.jointJogPublisher = self.create_publisher(JointJog, '/servo_node/delta_joint_cmds', 10)
        self.get_logger().info("ign_moveit2_py initialised successfuly")
        self.speed = 'auto'

    def get_speed(self):
        return self.speed

    def log(self, message):
        self.get_logger().info(message)

    def init_robot(self, robot_model, separate_gripper_controller):
        """
        Initialise robot groups, links and joints. This would normally get loaded from URDF via
        `moveit_commander`.
        This also initialises subscriber to joint states and publisher to joint trajectories.
        """
        if 'panda' == robot_model:
            self.robot_group_name = "panda_arm_hand"
            # Arm
            self.arm_group_name = "panda_arm"
            self.arm_joints = ["panda_joint1",
                               "panda_joint2",
                               "panda_joint3",
                               "panda_joint4",
                               "panda_joint5",
                               "panda_joint6",
                               "panda_joint7"]
            self.arm_links = ["panda_link0",
                              "panda_link1",
                              "panda_link2",
                              "panda_link3",
                              "panda_link4",
                              "panda_link5",
                              "panda_link6",
                              "panda_link7",
                              "panda_link8",
                              "gripper"]
            self.arm_base_link = self.arm_links[0]
            self.arm_end_effector = self.arm_links[-1]
            # Gripper
            self.gripper_group_name = "hand"
            self.gripper_joints = ["panda_finger_joint1",
                                   "panda_finger_joint2"]
            self.gripper_max_width = 0.08
            self.gripper_max_speed = 0.2
            self.gripper_max_force = 20.0
        elif 'ur5_rg2' == robot_model:
            self.robot_group_name = "ur5_rg2"
            # Arm
            self.arm_group_name = "ur5"
            self.arm_joints = ["elbow_joint",
                               "shoulder_lift_joint",
                               "shoulder_pan_joint",
                               "wrist_1_joint",
                               "wrist_2_joint",
                               "wrist_3_joint"]
            self.arm_links = ["base_link",
                              "shoulder_link",
                              "upper_arm_link",
                              "forearm_link",
                              "wrist_1_link",
                              "wrist_2_link",
                              "wrist_3_link",
                              "tool0"]
            self.arm_base_link = self.arm_links[0]
            self.arm_end_effector = self.arm_links[-1]
            # Gripper
            self.gripper_group_name = "rg2"
            self.gripper_joints = ["rg2_finger_joint1",
                                   "rg2_finger_joint2"]
            self.gripper_max_width = 1.0471976
            self.gripper_max_speed = 1.57
            self.gripper_max_force = 10.6
        elif 'ur10e' == robot_model:
            self.robot_group_name = "ur10e"
            # Arm
            self.arm_group_name = "manipulator"
            self.arm_joints = ["elbow_joint",
                               "shoulder_lift_joint",
                               "shoulder_pan_joint",
                               "wrist_1_joint",
                               "wrist_2_joint",
                               "wrist_3_joint"]
            self.arm_links = ["base_link",
                              "shoulder_link",
                              "upper_arm_link",
                              "forearm_link",
                              "wrist_1_link",
                              "wrist_2_link",
                              "wrist_3_link",
                              "tool0",
                              "gripper"]
            self.arm_base_link = self.arm_links[0]
            self.arm_end_effector = self.arm_links[-1]
            # Gripper
            self.gripper_group_name = "rg2"
            self.gripper_joints = ["rg2_finger_joint1",
                                   "rg2_finger_joint2"]
            self.gripper_max_width = 1.0471976
            self.gripper_max_speed = 1.57
            self.gripper_max_force = 10.6
        elif 'tm12' == robot_model:
            self.robot_group_name = "tm12"
            # Arm
            self.arm_group_name = "tmr_arm"
            self.arm_joints = ["joint_1",
                               "joint_2",
                               "joint_3",
                               "joint_4",
                               "joint_5",
                               "joint_6"]
            self.arm_links = ["slider_slider",
                              "base_link",
                              "shoulder_1_link",
                              "arm_1_link",
                              "arm_2_link",
                              "wrist_1_link",
                              "wrist_2_link",
                              "wrist_3_link",
                              "tool0",
                              "gripper"]
            self.arm_base_link = self.arm_links[0]
            self.arm_end_effector = self.arm_links[-1]
            # Gripper
            self.gripper_group_name = "rg2"
            self.gripper_joints = ["rg2_finger_joint1",
                                   "rg2_finger_joint2"]
            self.gripper_max_width = 1.0471976
            self.gripper_max_speed = 1.57
            self.gripper_max_force = 10.6

        # Publisher of trajectories
        self.joint_trajectory_pub = self.create_publisher(JointTrajectory,
                                                          "joint_trajectory", 1)

        self.use_separate_gripper_controller = separate_gripper_controller
        if separate_gripper_controller:
            self.gripper_trajectory_pub = self.create_publisher(JointTrajectory,
                                                                "gripper_trajectory", 1)

        # Subscriber of current joint states
        self.joint_state = JointState()
        self.joint_state_mutex = threading.Lock()
        self.joint_state_sub = self.create_subscription(JointState,
                                                        "joint_states",
                                                        self.joint_state_callback, 1)

        # Subscriber of joint trajectory progress
        self.joint_progress = 1.0
        self.joint_progress_cond = threading.Condition()
        self.joint_progress_sub = self.create_subscription(Float32,
                                                           "joint_trajectory_progress",
                                                           self.joint_progress_callback, 1)
        self.got_joint_state = None                                                   
    def joint_state_callback(self, msg):
        """
        Callback for getting current joint states.
        """
        self.joint_state_mutex.acquire()
        self.joint_state = msg
        if self.got_joint_state == None:
            self.got_joint_state = msg
        self.joint_state_mutex.release()

    def get_joint_state(self) -> JointState:
        """
        Get current joint states.
        """
        self.joint_state_mutex.acquire()
        joint_state = self.joint_state
        self.joint_state_mutex.release()
        return joint_state

    def joint_progress_callback(self, msg):
        """
        Callback for getting joint trajectory progress.
        """
        with self.joint_progress_cond:
            self.joint_progress = msg.data
            self.joint_progress_cond.notify_all()

    def wait_until_executed(self):
        """
        Function that halts execution on the current thread until trajectory is executed.
        """
        with self.joint_progress_cond:
            while not self.joint_progress == 1.0:
                self.joint_progress_cond.wait(timeout=0.5)

    def pub_trajectory(self, trajectory, is_gripper=False):
        """
        Publish trajectory such that it can be executed, e.g. by `JointTrajectoryController`
        Ignition plugin.
        """
        if isinstance(trajectory, JointTrajectory):
            if is_gripper and self.use_separate_gripper_controller:
                self.gripper_trajectory_pub.publish(trajectory)
            else:
                self.joint_trajectory_pub.publish(trajectory)
                goal = ExecuteTrajectory.Goal()
                robot_traj = RobotTrajectory()
                robot_traj.joint_trajectory = trajectory
                goal.trajectory = robot_traj
                return self.execute_trajectory_client.send_goal_async(goal)
        elif isinstance(trajectory, RobotTrajectory):
            if is_gripper and self.use_separate_gripper_controller:
                self.gripper_trajectory_pub.publish(
                    trajectory.joint_trajectory)
            else:
                self.joint_trajectory_pub.publish(trajectory.joint_trajectory)
                goal = ExecuteTrajectory.Goal()
                goal.trajectory = trajectory
                return self.execute_trajectory_client.send_goal_async(goal)
        else:
            raise Exception("Invalid type passed into pub_trajectory()")

    def get_trajectory_result(self, goal_handle):
        return goal_handle.get_result_async()

    async def execute(self, joint_trajectory=None, is_gripper=False, wait_for_result=False) -> bool:
        """
        Execute last planned motion plan, or the `joint_trajectory` specified as argument.
        """
        self.get_logger().info("Starting execution")
        if joint_trajectory == None:
            plan = self.motion_plan_.joint_trajectory
        else:
            plan = joint_trajectory

        # Make sure there is a plan to follow
        if not plan.points:
            # TODO: re-enable warning, but add an optional debug level to configuration
            self.get_logger().warn(
                "Cannot execute motion plan because it does not contain any trajectory points")
            return False

        # # # current errors
        # if self.speed < 100.0:
        #     for point in plan.points:       
        #         nano_sec = int(point.time_from_start.nanosec / self.speed * 100.0)
        #         sec = int(point.time_from_start.sec / self.speed * 100.0) + int(math.floor(nano_sec / 1000000000))
        #         nano_sec = int(nano_sec % 1000000000)

        #         self.log("point from: " + str(point.time_from_start) + " to: " + str(sec)+ ":" + str((int( nano_sec + int((sec - int(math.floor(sec))) * 1000000000)))))
        #         point.time_from_start.sec = int(math.floor(sec))
        #         point.time_from_start.nanosec = nano_sec + int((sec - int(math.floor(sec))) * 1000000000)
# # current errors
        # if self.speed < 100.0:
        #     for point in plan.points:       
        #         nano_sec = int(point.time_from_start.nanosec / self.speed * 100.0)
        #         sec = int(point.time_from_start.sec / self.speed * 100.0) + int(math.floor(nano_sec / 1000000000))
        #         nano_sec = int(nano_sec % 1000000000)

        #         self.log("point from: " + str(point.time_from_start) + " to: " + str(sec)+ ":" + str((int( nano_sec + int((sec - int(math.floor(sec))) * 1000000000)))))
        #         point.time_from_start.sec = int(math.floor(sec))
        #         point.time_from_start.na
        # Reset joint progress
        self.joint_progress = 0.0

        goal = await self.pub_trajectory(plan, is_gripper=is_gripper)
        if wait_for_result:
            result = await self.get_trajectory_result(goal)
        return True

    def retime_trajectory(self, ref_state_in, traj_in, velocity_scaling_factor):
        ser_ref_state_in = ref_state_in.to_msg() # conversions.msg_to_string(ref_state_in)
        ser_traj_in = traj_in.to_msg() #conversions.msg_to_string(traj_in)
        ser_traj_out = self._g.retime_trajectory(ser_ref_state_in, ser_traj_in, velocity_scaling_factor)

    def set_speed(self, speedPercentage: str):
        self.speed = int(speedPercentage) * 1.0 if speedPercentage is not 'auto' else speedPercentage

    def move_to_joint_state(self, joint_state,
                            set_position=True,
                            set_velocity=True,
                            set_effort=True):
        """
        Set joint target on all joints defined in `joint_state`. This function does NOT plan a
        smooth trajectory and only publishes joint_state as the next goal that should be reached
        immediately.
        """
        joint_trajectory = JointTrajectory()
        joint_trajectory.joint_names = joint_state.name

        point = JointTrajectoryPoint()
        if set_position:
            point.positions = joint_state.position
        if set_velocity:
            point.velocities = joint_state.velocity
        if set_effort:
            point.effort = joint_state.effort
        joint_trajectory.points.append(point)

        self.pub_trajectory(joint_trajectory)

    def move_to_joint_positions(self, joint_positions):
        """
        Set joint position target on all joints. This function does NOT plan a
        smooth trajectory and only publishes joint_state as the next goal that should be reached
        immediately.
        """

        joint_trajectory = JointTrajectory()
        joint_trajectory.header.frame_id = "world" # self.arm_base_link
        joint_trajectory.joint_names = self.arm_joints
        point = JointTrajectoryPoint()
        point.positions = joint_positions
        joint_trajectory.points.append(point)
        self.pub_trajectory(joint_trajectory)

    def init_execute_trajectory(self):
        self.execute_trajectory_client = ActionClient(self, ExecuteTrajectory, 'execute_trajectory')
        while not self.execute_trajectory_client.wait_for_server(timeout_sec=1.0):
            self.get_logger().info(
                "Server [execute_trajectory] not currently available, waiting...")

                
    # compute_fk
    def init_compute_fk(self):
        """
        Initialise `compute_fk` service.
        """
        self.compute_fk_client = self.create_client(GetPositionFK,
                                                    "compute_fk")
        while not self.compute_fk_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(
                "Service [compute_fk] not currently available, waiting...")

        self.fk_request = GetPositionFK.Request()
        self.fk_request.header.frame_id = "world" #self.arm_base_link
        # self.fk_request.header.stamp = "Set during request"
        # self.fk_request.fk_link_names = "Set during request"
        # self.fk_request.robot_state.joint_state = "Set during request"
        # self.fk_request.robot_state.multi_dof_joint_state = "Ignored"
        # self.fk_request.robot_state.attached_collision_objects = "Ignored"
        self.fk_request.robot_state.is_diff = False


    def compute_fk(self, fk_link_names=None, joint_state=None) -> GetPositionFK.Response:
        """
        Call `compute_fk` service.
        """
        if fk_link_names == None:
            self.fk_request.fk_link_names = [self.arm_end_effector]
        else:
            self.fk_request.fk_link_names = fk_link_names

        if joint_state == None:
            self.fk_request.robot_state.joint_state = self.get_joint_state()
        else:
            self.fk_request.robot_state.joint_state = joint_state

        self.fk_request.header.stamp = self.fk_request.robot_state.joint_state.header.stamp
        self.compute_fk_client.wait_for_service()
        return self.compute_fk_client.call(self.fk_request)

    # compute_ik
    def init_compute_ik(self):
        """
        Initialise `compute_ik` service.
        """
        # Service client for IK
        self.compute_ik_client = self.create_client(GetPositionIK,
                                                    "compute_ik")
        while not self.compute_ik_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(
                "Service [compute_ik] not currently available, waiting...")

        self.ik_request = GetPositionIK.Request()
        self.ik_request.ik_request.group_name = self.arm_group_name
        # self.ik_request.ik_request.robot_state.joint_state = "Set during request"
        # self.ik_request.ik_request.robot_state.multi_dof_joint_state = "Ignored"
        # self.ik_request.ik_request.robot_state.attached_collision_objects = "Ignored"
        self.ik_request.ik_request.robot_state.is_diff = False
        # self.ik_request.ik_request.constraints = "Set during request OR Ignored"
        self.ik_request.ik_request.avoid_collisions = True
        self.ik_request.ik_request.ik_link_name = self.arm_end_effector #"Ignored"
        self.ik_request.ik_request.pose_stamped.header.frame_id = "world" # self.arm_base_link
        # self.ik_request.ik_request.pose_stamped.header.stamp = "Set during request"
        # self.ik_request.ik_request.pose_stamped.pose = "Set during request"
        # self.ik_request.ik_request.ik_link_names = "Ignored"
        # self.ik_request.ik_request.pose_stamped_vector = "Ignored"
        # self.ik_request.ik_request.timeout.sec = 5.0 # "Ignored"
        # self.ik_request.ik_request.timeout.nanosec = "Ignored"

    def compute_ik(self, pose, start_joint_state=None, constrains=None) -> GetPositionIK.Response:
        """
        Call `compute_ik` service.
        """
        if start_joint_state == None:
            self.ik_request.ik_request.robot_state.joint_state = self.get_joint_state()
        else:
            self.ik_request.ik_request.robot_state.joint_state = start_joint_state

        if constrains != None:
            self.ik_request.ik_request.constraints = constrains

        self.ik_request.ik_request.pose_stamped.pose = pose

        self.ik_request.ik_request.pose_stamped.header.stamp = self._clock.now().to_msg()
        self.compute_ik_client.wait_for_service()
        return self.compute_ik_client.call(self.ik_request)

    # plan_kinematic_path
    def init_plan_kinematic_path(self):
        """
        Initialise `plan_kinematic_path` service.
        """
        # Service client for IK
        self.plan_kinematic_path_client = self.create_client(GetMotionPlan,
                                                             "plan_kinematic_path")
        while not self.plan_kinematic_path_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(
                "Service [plan_kinematic_path] not currently available, waiting...")

        self.kinematic_path_request = GetMotionPlan.Request()
        self.kinematic_path_request.motion_plan_request.workspace_parameters.header.frame_id = \
            "world" #self.arm_base_link
        # self.kinematic_path_request.motion_plan_request.workspace_parameters.header.stamp = \
        # "Set during request"
        self.kinematic_path_request.motion_plan_request.workspace_parameters.min_corner.x = 0.0
        self.kinematic_path_request.motion_plan_request.workspace_parameters.min_corner.y = 0.0
        self.kinematic_path_request.motion_plan_request.workspace_parameters.min_corner.z = 0.0
        self.kinematic_path_request.motion_plan_request.workspace_parameters.max_corner.x = 2.4
        self.kinematic_path_request.motion_plan_request.workspace_parameters.max_corner.y = 6.0
        self.kinematic_path_request.motion_plan_request.workspace_parameters.max_corner.z = 2.4
        # self.kinematic_path_request.motion_plan_request.start_state = "Ignored"
        self.kinematic_path_request.motion_plan_request.goal_constraints = \
            [Constraints()]
        # self.kinematic_path_request.motion_plan_request.path_constraints = "Ignored"
        # self.kinematic_path_request.motion_plan_request.trajectory_constraints = "Ignored"
        # self.kinematic_path_request.motion_plan_request.reference_trajectories = "Ignored"
        # self.kinematic_path_request.motion_plan_request.planner_id = "BiTRRT"
        self.kinematic_path_request.motion_plan_request.pipeline_id = "move_group"
        self.kinematic_path_request.motion_plan_request.group_name = self.arm_group_name
        # self.kinematic_path_request.motion_plan_request.num_planning_attempts = \
        # "Set during request"
        # self.kinematic_path_request.motion_plan_request.allowed_planning_time = \
        # "Set during request"
        self.kinematic_path_request.motion_plan_request.max_velocity_scaling_factor = 0.0
        self.kinematic_path_request.motion_plan_request.max_acceleration_scaling_factor = 0.0
        self.kinematic_path_request.motion_plan_request.cartesian_speed_end_effector_link = \
            self.arm_end_effector
        self.kinematic_path_request.motion_plan_request.max_cartesian_speed = 0.0

    def init_plan_pilz_path(self):
        """
        Initialise `plan_pilz_path` service.
        """

        self.pilz_path_request = GetMotionPlan.Request()
        self.pilz_path_request.motion_plan_request.workspace_parameters.header.frame_id = \
            "world" #self.arm_base_link
        # self.pilz_path_request.motion_plan_request.workspace_parameters.header.stamp = \
        # "Set during request"
        self.pilz_path_request.motion_plan_request.workspace_parameters.min_corner.x = 0.0
        self.pilz_path_request.motion_plan_request.workspace_parameters.min_corner.y = 0.0
        self.pilz_path_request.motion_plan_request.workspace_parameters.min_corner.z = 0.0
        self.pilz_path_request.motion_plan_request.workspace_parameters.max_corner.x = 2.4
        self.pilz_path_request.motion_plan_request.workspace_parameters.max_corner.y = 6.0
        self.pilz_path_request.motion_plan_request.workspace_parameters.max_corner.z = 2.4
        # self.pilz_path_request.motion_plan_request.start_state = "Ignored"
        self.pilz_path_request.motion_plan_request.goal_constraints = \
            [Constraints()]
        # self.pilz_path_request.motion_plan_request.path_constraints = "Ignored"
        # self.pilz_path_request.motion_plan_request.trajectory_constraints = "Ignored"
        # self.pilz_path_request.motion_plan_request.reference_trajectories = "Ignored"
        self.pilz_path_request.motion_plan_request.planner_id = "PTP"
        self.pilz_path_request.motion_plan_request.pipeline_id = "pilz"
        self.pilz_path_request.motion_plan_request.group_name = self.arm_group_name
        self.pilz_path_request.motion_plan_request.group_name = self.arm_group_name
        # self.pilz_path_request.motion_plan_request.num_planning_attempts = \
        # "Set during request"
        # self.pilz_path_request.motion_plan_request.allowed_planning_time = \
        # "Set during request"
        self.pilz_path_request.motion_plan_request.max_velocity_scaling_factor = 0.0
        self.pilz_path_request.motion_plan_request.max_acceleration_scaling_factor = 0.0
        self.pilz_path_request.motion_plan_request.cartesian_speed_end_effector_link = \
            self.arm_end_effector
        self.pilz_path_request.motion_plan_request.max_cartesian_speed = 0.0
    def set_max_velocity(self, scaling_factor):
        """
        Set maximum velocity of joints as a factor of joint limits.
        """
        self.kinematic_path_request.motion_plan_request.max_velocity_scaling_factor = scaling_factor

    def set_max_acceleration(self, scaling_factor):
        """
        Set maximum acceleration of joints as a factor of joint limits.
        """
        self.kinematic_path_request.motion_plan_request.max_acceleration_scaling_factor = \
            scaling_factor

    def set_max_cartesian_speed(self, speed):
        """
        Set maximum cartesian speed of end effector.
        """
        self.kinematic_path_request.motion_plan_request.max_cartesian_speed = speed

    def set_max_velocity_pilz(self, scaling_factor):
        """
        Set maximum velocity of joints as a factor of joint limits.
        """
        self.pilz_path_request.motion_plan_request.max_velocity_scaling_factor = scaling_factor

    def set_max_acceleration_pilz(self, scaling_factor):
        """
        Set maximum acceleration of joints as a factor of joint limits.
        """
        self.pilz_path_request.motion_plan_request.max_acceleration_scaling_factor = \
            scaling_factor

    def set_max_cartesian_speed_pilz(self, speed):
        """
        Set maximum cartesian speed of end effector.
        """
        self.pilz_path_request.motion_plan_request.max_cartesian_speed = speed

    async def plan_kinematic_path(self,
                            allowed_planning_time=5.0,
                            num_planning_attempts=10, attached_collision_objects = [], path_constraints = None, joint_state = None, auto_speed = 1.0) -> GetMotionPlan.Response:
        """
        Call `plan_kinematic_path` service, with goal set using either `set_joint_goal()`,
        `set_position_goal()`, `set_orientation_goal()` or `set_pose_goal()`.
        """

        self.kinematic_path_request.motion_plan_request.num_planning_attempts = \
            num_planning_attempts
        self.kinematic_path_request.motion_plan_request.allowed_planning_time = \
            allowed_planning_time

        self.kinematic_path_request.motion_plan_request.workspace_parameters.header.stamp = \
            self._clock.now().to_msg()

        # Stamp message with current time
        clock_time_now_msg = self._clock.now().to_msg()
        self.kinematic_path_request.motion_plan_request.workspace_parameters.header.stamp = \
            clock_time_now_msg
        for contraints in self.kinematic_path_request.motion_plan_request.goal_constraints:
            for position_constraint in contraints.position_constraints:
                position_constraint.header.stamp = clock_time_now_msg
            for orientation_constraint in contraints.orientation_constraints:
                orientation_constraint.header.stamp = clock_time_now_msg
        # set robot state
        # self.kinematic_path_request.motion_plan_request.start_state.is_diff = True
        if joint_state == None:
            self.kinematic_path_request.motion_plan_request.start_state.joint_state = self.get_joint_state()
        else:
            self.kinematic_path_request.motion_plan_request.start_state.joint_state = joint_state
        self.kinematic_path_request.motion_plan_request.start_state.attached_collision_objects = attached_collision_objects

        self.kinematic_path_request.motion_plan_request.max_acceleration_scaling_factor = self.speed / 100.0 if self.speed is not 'auto' else auto_speed
        # if(path_constraints is not None):
        #     self.kinematic_path_request.motion_plan_request.path_constraints = path_constraints;
        # # set trajectory constraints
        # if reference_trajectories is not None:
        #     self.kinematic_path_request.motion_plan_request.reference_trajectories = reference_trajectories
        # else:
        #     self.kinematic_path_request.motion_plan_request.reference_trajectories = None
        self.plan_kinematic_path_client.wait_for_service()
        response = self.plan_kinematic_path_client.call(
            self.kinematic_path_request)
        self.clear_goal_constraints()
        self.motion_plan_ = response.motion_plan_response.trajectory

        return response

    async def plan_hybrid_path(self, goal_constraints, wait_for_result=False, attached_collision_objects = [], path_constraints = None):
        sequence_request = MotionSequenceRequest()
        # single goal constrain with multiple position & orientation constraints

        goal_motion_request = MotionPlanRequest()
        goal_motion_request.group_name = self.arm_group_name
        goal_motion_request.num_planning_attempts = 10
        goal_motion_request.allowed_planning_time = 2.0
        goal_motion_request.max_velocity_scaling_factor = 0.1
        goal_motion_request.max_acceleration_scaling_factor = 0.1
        # goal_motion_request.max_cartesian_speed = 0.1
        # goal_motion_request.cartesian_speed_end_effector_link = self.arm_end_effector
        goal_motion_request.workspace_parameters.min_corner.x = 0.01
        goal_motion_request.workspace_parameters.max_corner.z = 2.4
        goal_motion_request.workspace_parameters.min_corner.z = 0.01
        goal_motion_request.workspace_parameters.max_corner.x = 2.4
        goal_motion_request.workspace_parameters.max_corner.y = 6.0
        goal_motion_request.workspace_parameters.min_corner.y = 0.01
        # goal_motion_request.planner_id = "geometric::BiTRRT"
        goal_motion_request.planner_id = "RRTConnectkConfigDefault"
        goal_motion_request.pipeline_id =  "ompl"
            # goal_motion_request.planner_id = "ompl" # -=> no planner name => takes last groupname
            # goal_motion_request.pipeline_id = "move_group"
        goal_motion_request.goal_constraints = goal_constraints
        #  [Constraints()]
        # for goal in goal_constraints:
        #     position_constraint = PositionConstraint()
        #     position_constraint.target_point_offset.x = goal.position.x
        #     position_constraint.target_point_offset.y = goal.position.y
        #     position_constraint.target_point_offset.z = goal.position.z
        #     goal_motion_request.goal_constraints[0].position_constraints.push_back(position_constraint);

        #     orientation_constraint = OrientationConstraint()
        #     orientation_constraint.orientation = goal.orientation
        #     goal_motion_request.goal_constraints[0].orientation_constraints.push_back(orientation_constraint)

        for contraints in goal_motion_request.goal_constraints:
            self.get_logger().info("Constraint: " + str(contraints))
            # goal_motion_request.start_state.joint_state = self.get_joint_state()
            # goal_motion_request.start_state.attached_collision_objects = attached_collision_objects
        sequence_item = MotionSequenceItem()
        sequence_item.req = goal_motion_request
        # if goal == goal_constraints[-1]:
        #     sequence_item.blend_radius = 0.0 #0 for single goal
        # else:
        #     sequence_item.blend_radius = 0.01

        
        sequence_request.items.append(sequence_item)


        goal_action_request = HybridPlanner.Goal()
        goal_action_request.planning_group = self.arm_group_name
        # goal_action_request.
        goal_action_request.motion_sequence = sequence_request

        # goalRequest = HybridPlanning.Request()
        # goalRequest. = goal_action_request
        self.get_logger().info("Planning hybrid path" + str(goal_action_request))
        goal = await self.hp_action_client.send_goal_async(goal_action_request, self.feedback_callback)
        
        # self.motion_plan_ = goal.motion_plan_response.trajectory
        # result = await self.get_trajectory_result(goal)
        # self.get_logger().info(str(result))
        self.get_logger().info("goal: " + str(goal))
        self.clear_goal_constraints()
        if wait_for_result:
            result = await goal.get_result_async()
            self.get_logger().info('Goal result:')
            self.get_logger().info(str(result))
        
        return goal

    def feedback_callback(self, feedback):
        self.get_logger().info('Feedback received')
        self.get_logger().info('Feedback received: %s' % feedback)

    def clear_goal_constraints(self):
        """
        Clear all goal constraints that were previously set.
        Note that this function is called automatically after each `plan_kinematic_path()`.
        """
        self.kinematic_path_request.motion_plan_request.goal_constraints = \
            [Constraints()]

    def clear_goal_constraints_pilz(self):
        """
        Clear all goal constraints that were previously set.
        Note that this function is called automatically after each `plan_kinematic_path()`.
        """
        self.pilz_path_request.motion_plan_request.goal_constraints = \
            [Constraints()]

    def create_new_goal_constraint(self):
        """
        Create a new set of goal contraints that will be set together with the request. Each
        subsequent setting of goals with `set_joint_goal()`, `set_pose_goal()` and others will be
        added under this newly created set of contraints.
        """
        (self.kinematic_path_request.motion_plan_request.goal_constraints
         .append(Constraints()))

    def create_joint_goal(self, joint_positions, tolerance=0.001, weight=1.0, joint_names=None):
        if joint_names == None:
            joint_names = self.arm_joints
        joint_constraints = []

        for i in range(min(len(joint_positions), 6)):
            joint_constraint = JointConstraint()
            joint_constraint.joint_name = joint_names[i]
            joint_constraint.position = joint_positions[i]
            joint_constraint.tolerance_above = tolerance
            joint_constraint.tolerance_below = tolerance
            joint_constraint.weight = weight

            (joint_constraints.append(joint_constraint))
        return joint_constraints

    def set_joint_goal(self, joint_positions, tolerance=0.001, weight=1.0, joint_names=None):
        """
        Set goal position in joint space. With `joint_names` specified, `joint_positions` can be
        defined for specific joints. Otherwise, first `n` joints defined in `init_robot()` will be
        used, where `n` is the length of `joint_positions`.
        """
        if joint_names == None:
            joint_names = self.arm_joints

        for i in range(min(len(joint_positions), 6)):
            joint_constraint = JointConstraint()
            joint_constraint.joint_name = joint_names[i]
            joint_constraint.position = joint_positions[i]
            joint_constraint.tolerance_above = tolerance
            joint_constraint.tolerance_below = tolerance
            joint_constraint.weight = weight

            (self.kinematic_path_request.motion_plan_request.goal_constraints[-1].
             joint_constraints.append(joint_constraint))
    def set_joint_goal_pilz(self, joint_positions, tolerance=0.001, weight=1.0, joint_names=None):
        """
        Set goal position in joint space. With `joint_names` specified, `joint_positions` can be
        defined for specific joints. Otherwise, first `n` joints defined in `init_robot()` will be
        used, where `n` is the length of `joint_positions`.
        """
        if joint_names == None:
            joint_names = self.arm_joints

        for i in range(min(len(joint_positions), 6)):
            joint_constraint = JointConstraint()
            joint_constraint.joint_name = joint_names[i]
            joint_constraint.position = joint_positions[i]
            joint_constraint.tolerance_above = tolerance
            joint_constraint.tolerance_below = tolerance
            joint_constraint.weight = weight

            (self.pilz_path_request.motion_plan_request.goal_constraints[-1].
             joint_constraints.append(joint_constraint))

    def create_position_goal(self, position, tolerance=0.001, weight=1.0, frame=None):
        if frame == None:
            frame = self.arm_base_link

        position_constraint = PositionConstraint()
        position_constraint.header.frame_id = "world" #frame
        position_constraint.link_name = self.arm_end_effector
        
        # position_constraint.target_point_offset.x = 0.005
        # position_constraint.target_point_offset.y = 0.005
        # position_constraint.target_point_offset.z = 0.005 # = position 
        position_constraint.constraint_region.primitive_poses.append(Pose())
        position_constraint.constraint_region.primitive_poses[0].position.x = float(
            position.x)
        position_constraint.constraint_region.primitive_poses[0].position.y = float(
            position.y)
        position_constraint.constraint_region.primitive_poses[0].position.z = float(
            position.z)

        # Goal is defined as a sphere with radius equal to tolerance
        position_constraint.constraint_region.primitives.append(
            SolidPrimitive())
        position_constraint.constraint_region.primitives[0].type = 2
        position_constraint.constraint_region.primitives[0].dimensions = [
            tolerance]
        position_constraint.weight = weight

        return position_constraint
    def set_position_goal(self, position, tolerance=0.001, weight=1.0, frame=None):
        """
        Set goal position of `frame` in Cartesian space. Defaults to the end-effector `frame`.
        """
        position_constraint = self.create_position_goal(position, tolerance, weight, frame)

        (self.kinematic_path_request.motion_plan_request.goal_constraints[-1].position_constraints
         .append(position_constraint))

    def set_position_goal_pilz(self, position, tolerance=0.001, weight=1.0, frame=None):
        """
        Set goal position of `frame` in Cartesian space. Defaults to the end-effector `frame`.
        """
        position_constraint = self.create_position_goal(position, tolerance, weight, frame)

        (self.pilz_path_request.motion_plan_request.goal_constraints[-1].position_constraints
         .append(position_constraint))

    def create_orientation_goal(self, quaternion, tolerance=0.001, weight=1.0, frame=None):
        if frame == None:
            frame = self.arm_end_effector

        orientation_constraint = OrientationConstraint()
        orientation_constraint.header.frame_id = "world" # self.arm_base_link
        orientation_constraint.link_name = frame
        # orientation_constraint.orientation.x = float(quaternion[0])
        # orientation_constraint.orientation.y = float(quaternion[1])
        # orientation_constraint.orientation.z = float(quaternion[2])
        # orientation_constraint.orientation.w = float(quaternion[3])
        orientation_constraint.orientation = quaternion
        orientation_constraint.absolute_x_axis_tolerance = tolerance
        orientation_constraint.absolute_y_axis_tolerance = tolerance
        orientation_constraint.absolute_z_axis_tolerance = tolerance
        orientation_constraint.weight = weight

        return orientation_constraint

    def set_orientation_goal(self, quaternion, tolerance=0.001, weight=1.0, frame=None):
        """
        Set goal orientation of `frame`. Defaults to the end-effector `frame`.
        """

        orientation_constraint = self.create_orientation_goal(quaternion=quaternion,tolerance=tolerance,weight=weight,frame=frame)
        (self.kinematic_path_request.motion_plan_request.goal_constraints[-1]
         .orientation_constraints.append(orientation_constraint))

    def set_orientation_goal_pilz(self, quaternion, tolerance=0.001, weight=1.0, frame=None):
        """
        Set goal orientation of `frame`. Defaults to the end-effector `frame`.
        """

        orientation_constraint = self.create_orientation_goal(quaternion=quaternion,tolerance=tolerance,weight=weight,frame=frame)
        (self.pilz_path_request.motion_plan_request.goal_constraints[-1]
         .orientation_constraints.append(orientation_constraint))

    def set_pose_goal(self, position, quaternion,
                      tolerance_position=0.001, tolerance_orientation=0.001,
                      weight_position=1.0, weight_orientation=1.0,
                      frame=None):
        """
        Set goal pose. This is direct combination of `set_position_goal()` and
        `set_orientation_goal()`.
        """
        self.set_position_goal(
            position, tolerance_position, weight_position, frame)
        self.set_orientation_goal(
            quaternion, tolerance_orientation, weight_orientation, frame)

    def set_pose_goal_pilz(self, position, quaternion,
                      tolerance_position=0.001, tolerance_orientation=0.001,
                      weight_position=1.0, weight_orientation=1.0,
                      frame=None):
        """
        Set goal pose. This is direct combination of `set_position_goal()` and
        `set_orientation_goal()`.
        """
        self.set_position_goal_pilz(
            position, tolerance_position, weight_position, frame)
        self.set_orientation_goal_pilz(
            quaternion, tolerance_orientation, weight_orientation, frame)

    # plan_cartesian_path
    def init_plan_cartesian_path(self):
        """
        Initialise `compute_cartesian_path` service.
        """
        # Service client for IK
        self.plan_cartesian_path_client = self.create_client(GetCartesianPath,
                                                             "compute_cartesian_path")
        while not self.plan_cartesian_path_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(
                "Service [compute_cartesian_path] not currently available, waiting...")

        self.cartesian_path_request = GetCartesianPath.Request()
        self.cartesian_path_request.header.frame_id = \
            "world" #self.arm_base_link
        # self.kinematic_path_request.start_state = "Ignored"
        self.cartesian_path_request.group_name = self.arm_group_name
        self.cartesian_path_request.link_name = self.arm_end_effector
        self.cartesian_path_request.waypoints = []
        self.cartesian_path_request.max_step = 0.01
        self.cartesian_path_request.jump_threshold = 5.0 # 5.0 #0.01
        # self.cartesian_path_request.prismatic_jump_threshold = 0.0
        # self.cartesian_path_request.revolute_jump_threshold = 0.0
        self.cartesian_path_request.avoid_collisions = True
        # self.cartesian_path_request.path_constraints = "Ignored"

    def plan_cartesian_path(self, waypoints: Sequence[Pose], constraints: Constraints, attached_collision_objects = []) -> GetCartesianPath.Response:
        """
        Call `compute_cartesian_path` service.
        """
        
        self.cartesian_path_request.header.stamp = \
            self._clock.now().to_msg()

        # Stamp message with current time
        clock_time_now_msg = self._clock.now().to_msg()
        for position_constraint in constraints.position_constraints:
            position_constraint.header.stamp = clock_time_now_msg
        for orientation_constraint in constraints.orientation_constraints:
            orientation_constraint.header.stamp = clock_time_now_msg
        
        self.cartesian_path_request.path_constraints = constraints
        self.cartesian_path_request.waypoints = waypoints
        # set robot state
        self.cartesian_path_request.start_state.joint_state = self.get_joint_state()
        self.cartesian_path_request.start_state.attached_collision_objects = \
            attached_collision_objects

        self.plan_cartesian_path_client.wait_for_service()
        response = self.plan_cartesian_path_client.call(
            self.cartesian_path_request)
        self.motion_plan_ = response.solution

        return response
    def run_servo_to_joints(self, joint_positions):
        joint_jog = JointJog()
        joint_jog.header.stamp = self.get_clock().now().to_msg()
        joint_jog.header.frame_id = "world"# self.arm_bas
        joint_jog.joint_names = self.arm_joints
        current_joint_state = self.get_joint_state()
        joint_jog.duration = 1.0
        # self.get_logger().info("Iterating")
        for index, joint in enumerate(joint_jog.joint_names):
            joint_jog.displacements.append(joint_positions[index] - current_joint_state.position[index])
            joint_jog.velocities.append(10.0)
            # self.get_logger().info("getting joint " + joint + ": " + str(joint_jog.displacements[index]))
       
        self.jointJogPublisher.publish(joint_jog)


    async def plan_pilz_path(self, allowed_planning_time=5.0,
                            num_planning_attempts=10, attached_collision_objects = [], path_constraints = None, joint_state = None, auto_speed = 1.0) -> GetMotionPlan.Response:
        """
        Call `plan_kinematic_path` service, with goal set using either `set_joint_goal()`,
        `set_position_goal()`, `set_orientation_goal()` or `set_pose_goal()`.
        """

        self.pilz_path_request.motion_plan_request.num_planning_attempts = \
            num_planning_attempts
        self.pilz_path_request.motion_plan_request.allowed_planning_time = \
            allowed_planning_time

        self.pilz_path_request.motion_plan_request.workspace_parameters.header.stamp = \
            self._clock.now().to_msg()

        # Stamp message with current time
        clock_time_now_msg = self._clock.now().to_msg()
        self.pilz_path_request.motion_plan_request.workspace_parameters.header.stamp = \
            clock_time_now_msg
        for contraints in self.pilz_path_request.motion_plan_request.goal_constraints:
            for position_constraint in contraints.position_constraints:
                position_constraint.header.stamp = clock_time_now_msg
            for orientation_constraint in contraints.orientation_constraints:
                orientation_constraint.header.stamp = clock_time_now_msg
        # set robot state
        self.pilz_path_request.motion_plan_request.start_state.is_diff = True
        if joint_state == None:
            self.pilz_path_request.motion_plan_request.start_state.joint_state = self.get_joint_state()
        else:
            self.pilz_path_request.motion_plan_request.start_state.joint_state = joint_state
        self.pilz_path_request.motion_plan_request.start_state.joint_state.velocity = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.pilz_path_request.motion_plan_request.start_state.attached_collision_objects = attached_collision_objects
        self.pilz_path_request.motion_plan_request.max_acceleration_scaling_factor = self.speed / 100.0 if self.speed is not 'auto' else auto_speed
        self.pilz_path_request.motion_plan_request.max_velocity_scaling_factor = self.speed / 100.0 if self.speed is not 'auto' else auto_speed
        self.pilz_path_request.motion_plan_request.max_cartesian_speed = 1.0
        # if(path_constraints is not None):
        #     self.kinematic_path_request.motion_plan_request.path_constraints = path_constraints;
        # # set trajectory constraints
        # if reference_trajectories is not None:
        #     self.kinematic_path_request.motion_plan_request.reference_trajectories = reference_trajectories
        # else:
        #     self.kinematic_path_request.motion_plan_request.reference_trajectories = None
        self.plan_kinematic_path_client.wait_for_service()
        response = self.plan_kinematic_path_client.call(
            self.pilz_path_request)
        self.clear_goal_constraints_pilz()
        self.motion_plan_pilz_ = response.motion_plan_response.trajectory

        return response
        
    async def plan_pilz_path_multipoint(self, goal_constraints, attached_collision_objects = [], path_constraints = [], auto_speed = 1.0) -> GetCartesianPath.Response:
        """
        Call `compute_cartesian_path` service.
        """
        sequence_request = MotionSequenceRequest()
        for goal in goal_constraints:
            goal_motion_request = MotionPlanRequest()
            goal_motion_request.group_name = self.arm_group_name
            goal_motion_request.num_planning_attempts = 10
            goal_motion_request.allowed_planning_time = 2.0
            goal_motion_request.max_acceleration_scaling_factor = self.speed / 100.0 if self.speed is not 'auto' else auto_speed
            goal_motion_request.max_velocity_scaling_factor = self.speed / 100.0 if self.speed is not 'auto' else auto_speed

            # goal_motion_request.max_cartesian_speed = 0.1
            # goal_motion_request.cartesian_speed_end_effector_link = self.arm_end_effector
            # goal_motion_request.workspace_parameters.min_corner.x = 0.0
            # goal_motion_request.workspace_parameters.max_corner.z = 2.4
            # goal_motion_request.workspace_parameters.min_corner.z = 0.0
            # goal_motion_request.workspace_parameters.max_corner.x = 2.4
            # goal_motion_request.workspace_parameters.max_corner.y = 6.0
            # goal_motion_request.workspace_parameters.min_corner.y = 0.0
            # goal_motion_request.planner_id = "geometric::BiTRRT"
            goal_motion_request.planner_id = "PTP"
            goal_motion_request.pipeline_id = "pilz"
            goal_motion_request.goal_constraints = [goal]
            goal_motion_request.start_state.joint_state = self.get_joint_state()
            goal_motion_request.start_state.attached_collision_objects = attached_collision_objects
            sequence_item = MotionSequenceItem()
            sequence_item.req = goal_motion_request
            if goal == goal_constraints[-1]:
                sequence_item.blend_radius = 0.0 #0 for single goal
            else:
                sequence_item.blend_radius = 0.00 # 0.01

            
            sequence_request.items.append(sequence_item)
        
        goal_action_request = MoveGroupSequence.Goal()
        goal_action_request.planning_options = PlanningOptions()
        # goal_action_request.planning_options.planning_scene_diff.is_diff = True
        goal_action_request.planning_options.plan_only = False
        # goal_action_request.planning_group = self.arm_group_name
        goal_action_request.request = sequence_request

        self.get_logger().info("Sending")
        # goalRequest = HybridPlanning.Request()
        # goalRequest. = goal_action_request
        goal = await self.movegroup_action_client.send_goal_async(goal_action_request, self.feedback_callback)
        self.get_logger().info("goal send")
        # self.motion_plan_ = goal.motion_plan_response.trajectory
        # result = await self.get_trajectory_result(goal)
        # self.get_logger().info(str(result))
        self.get_logger().info("goal: " + str(goal))
        
        return goal

    async def wait_for_goal(self, goal):
        result = await goal.get_result_async()
        self.get_logger().info('Goal result:')
        self.get_logger().info(str(result))
        return result

    # gripper
    def init_gripper(self):
        """
        Initialise gripper interface.
        """
        # Service client for IK
        self.plan_gripper_path_client = self.create_client(GetMotionPlan,
                                                           "plan_kinematic_path")
        while not self.plan_gripper_path_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info(
                "Service [plan_kinematic_path] not currently available, waiting...")

        self.gripper_path_request = GetMotionPlan.Request()
        self.gripper_path_request.motion_plan_request.workspace_parameters.header.frame_id = \
            "world" #self.arm_base_link
        # self.gripper_path_request.motion_plan_request.workspace_parameters.header.stamp = \
        # "Set during request"
        self.gripper_path_request.motion_plan_request.workspace_parameters.min_corner.x = -0.855
        self.gripper_path_request.motion_plan_request.workspace_parameters.min_corner.y = -0.855
        self.gripper_path_request.motion_plan_request.workspace_parameters.min_corner.z = -0.36
        self.gripper_path_request.motion_plan_request.workspace_parameters.max_corner.x = 0.855
        self.gripper_path_request.motion_plan_request.workspace_parameters.max_corner.y = 0.855
        self.gripper_path_request.motion_plan_request.workspace_parameters.max_corner.z = 1.19
        # self.gripper_path_request.motion_plan_request.start_state = "Ignored"
        self.gripper_path_request.motion_plan_request.goal_constraints = \
            [Constraints()]
        (self.gripper_path_request.motion_plan_request.goal_constraints[0].joint_constraints
         .append(JointConstraint()))
        (self.gripper_path_request.motion_plan_request.goal_constraints[0].joint_constraints[0]
         .joint_name) = self.gripper_joints[0]
        # (self.gripper_path_request.motion_plan_request.goal_constraints[0].joint_constraints[0]
        # .position) = "Set during request"
        # (self.gripper_path_request.motion_plan_request.goal_constraints[0].joint_constraints[0]
        # .tolerance_above) = "Ignored"
        # (self.gripper_path_request.motion_plan_request.goal_constraints[0].joint_constraints[0]
        # .tolerance_below) = "Ignored"
        self.gripper_path_request.motion_plan_request.goal_constraints[
            0].joint_constraints[0].weight = 1.0
        # self.gripper_path_request.motion_plan_request.path_constraints = "Ignored"
        # self.gripper_path_request.motion_plan_request.trajectory_constraints = "Ignored"
        # self.gripper_path_request.motion_plan_request.reference_trajectories = "Ignored"
        # self.gripper_path_request.motion_plan_request.planner_id = "Ignored"
        self.gripper_path_request.motion_plan_request.group_name = self.gripper_group_name
        self.gripper_path_request.motion_plan_request.num_planning_attempts = 1
        self.gripper_path_request.motion_plan_request.allowed_planning_time = 0.1
        # self.gripper_path_request.motion_plan_request.max_velocity_scaling_factor = \
        # "Set during request"
        self.gripper_path_request.motion_plan_request.max_acceleration_scaling_factor = 0.0
        # self.gripper_path_request.motion_plan_request.cartesian_speed_end_effector_link = \
        # "Ignored"
        # self.gripper_path_request.motion_plan_request.max_cartesian_speed = "Ignored"

    def gripper_plan_path(self, width, speed) -> JointTrajectory:
        """
        Plan kinematic path for gripper.
        """
        self.gripper_path_request.motion_plan_request.max_velocity_scaling_factor = \
            float(speed/self.gripper_max_speed)
        (self.gripper_path_request.motion_plan_request.goal_constraints[0].
         joint_constraints[0].position) = float(width/2)

        self.plan_gripper_path_client.wait_for_service()
        response = self.plan_gripper_path_client.call(
            self.gripper_path_request)

        joint_trajectory = response.motion_plan_response.trajectory.joint_trajectory

        # Mirror motion on the second finger (might be more efficient than double planning)
        joint_trajectory.joint_names.append(self.gripper_joints[1])
        for i in range(len(joint_trajectory.points)):
            if joint_trajectory.points[i].positions:
                (joint_trajectory.points[i].positions
                 .append(joint_trajectory.points[i].positions[0]))
            if joint_trajectory.points[i].velocities:
                (joint_trajectory.points[i].velocities
                 .append(joint_trajectory.points[i].velocities[0]))
            if joint_trajectory.points[i].accelerations:
                (joint_trajectory.points[i].accelerations
                 .append(joint_trajectory.points[i].accelerations[0]))
            if joint_trajectory.points[i].effort:
                (joint_trajectory.points[i].effort
                 .append(joint_trajectory.points[i].effort[0]))

        return joint_trajectory

    def gripper_close(self, width=0.0, speed=0.2, force: Optional[float] = None, force_start=0.75, manual_plan: bool = False) -> bool:
        """
        Close gripper. Argument `force_start` (0.0,1.0] can be used to indicate point of the
        trajectory at which force will start being applied.
        """

        if force is None:
            force = self.gripper_max_force

        if not manual_plan:
            joint_trajectory = self.gripper_plan_path(width, speed)
            if not joint_trajectory.points:
                # If planning failed, use manually planned trajectory
                joint_trajectory = self.__gripper_trajectory_manual(
                    width)
        else:
            joint_trajectory = self.__gripper_trajectory_manual(width)

        # Start slowly applying force in the last (1-force_start)% of the trajectory
        force_index_end = len(joint_trajectory.points)
        force_index_start = math.floor(force_start*force_index_end)
        force_range = max(1, force_index_end - force_index_start)
        for i in range(force_index_start, force_index_end):
            # Scale the applied force linearly with the index
            scaling_factor = ((i+1)-force_index_start) / force_range
            applied_force = scaling_factor * force
            # Closing direction is in negative directions
            joint_trajectory.points[i].effort = [-applied_force] * 2

        return self.execute(joint_trajectory, is_gripper=True)

    def gripper_open(self, width: Optional[float] = None, speed=0.2, manual_plan: bool = False) -> bool:
        """
        Open gripper.
        """

        if width is None:
            width = self.gripper_max_width

        if not manual_plan:
            joint_trajectory = self.gripper_plan_path(width, speed)
            if not joint_trajectory.points:
                # If planning failed, use manually planned trajectory
                joint_trajectory = self.__gripper_trajectory_manual(
                    width)
        else:
            joint_trajectory = self.__gripper_trajectory_manual(width)

        # Make sure no force is applied anymore
        joint_trajectory.points[0].effort = [0.0] * 2

        return self.execute(joint_trajectory, is_gripper=True)

    def __gripper_trajectory_manual(self, width: float) -> JointTrajectory:
        """
        Generate unnatural, instant trajectory. Use only if MoveIt planning fails for some reason
        """

        joint_trajectory = JointTrajectory()

        for gripper_joint in self.gripper_joints:
            joint_trajectory.joint_names.append(gripper_joint)

        joint_trajectory.header.stamp = self._clock.now().to_msg()
        joint_trajectory.header.frame_id = "world"# self.arm_base_link

        for i in range(2):
            joint_trajectory.points.append(JointTrajectoryPoint())
            joint_trajectory.points[i].positions = [width/2] * 2
            joint_trajectory.points[i].time_from_start.nanosec = int(2e8)

        return joint_trajectory

  