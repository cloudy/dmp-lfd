#!/usr/bin/env python

import numpy as np
import math
import rospy
import roslib
import tf
import intera_interface
import cv2 
import matplotlib
import matplotlib.pyplot as plt

from SawyerClass import Sawyer
from keras.models import load_model


plt.style.use('ggplot')
plt.rcParams['text.latex.preamble']=[r"\usepackage{lmodern}"]
#Options
params = {'text.usetex' : True,
          'font.size' : 11,
          'font.family' : 'lmodern',
          'text.latex.unicode': True,
          }
plt.rcParams.update(params) 

def psiF(h, c, s, i):
    return np.exp(-h[i]*(s-c[i])**2)

def plotGaussians(sv, gv, parameters, w_all, title="Gaussians"):
    colrs = ['b', 'g', 'r', 'c', 'm', 'y', 'k']
    if w_all is None: w_all = np.ones(parameters[0][gv].shape)
    plt.figure()
    plt.suptitle("%s" % title, fontsize= 18)
    for g in range(len(parameters)):
        plt.subplot(4, 1, g + 1)
        for i in range(len(parameters[g][gv])):
            plt.plot(parameters[g][sv], parameters[g][gv][i]*w_all[g][i], color = colrs[i % len(colrs)])
            plt.ylabel("q%s: $w_i \psi_i(s)$" % str(g + 1), fontsize=16)
            plt.xlabel("time (s)", fontsize=16)
            
    plt.grid()
    plt.show()

def DynamicMotionPrimitive(x, time, convert):
    time = time / convert  # Scale down time
    time = time - time[0]  # Make sure t[0] = 0
    dtx = np.diff(time)[0]  # TODO: should be dt
    dx = np.concatenate([[0], np.diff(x)]) / dtx  # Generate velocities
    ddx = np.concatenate([[0], np.diff(dx)]) / dtx  # Generate accelerations
    x0 = x[0]
    gx = x[-1]
    h = 1

    par = {}
    par['x']=x
    par['dx']=dx
    par['ddx']=ddx
    par['time']=time
    par['dt']=dtx
    par['x0']=x0
    par['gx']=gx
    par['ng'] = 30
    par['h'] = np.concatenate(np.ones((1, par['ng'])) * h)
    par['s'] = 1
    par['as'] = 6 
    par['tau'] = time[-1]
    par['K'] = 40000
    par['D'] = 100
    par['length'] = len(time)

    stime = []
    sE_x = np.array([])
    ftarget = np.array([])

    for i in range(0, len(time)):  # TODO: check end
        t = time[i]
        s = np.exp((-1 * par['as'] * t) / par['tau'])
        stime.append(s)
        ftar_tmp = (-1 * par['K'] * (gx - x[i]) + par['D'] * dx[i] + par['tau'] * ddx[i]) / (gx - x0)
        ftarget = np.append(ftarget, ftar_tmp)
        sE_x = np.append(sE_x, s * (gx - x0))

    # Calculate gaussian parameters
    # TODO: check if stime is in order, or err, smallest first largest last
    incr = (max(stime) - min(stime)) / (par['ng'] - 1)  # TODO: replace w/ index based min/max
    c = np.arange(min(stime), max(stime) + incr, incr)  # TODO: compare outputs
    lrc = c[::-1]
    ctime = (-1 * par['tau'] * np.log(lrc)) / par['as']
    d = np.diff(c)  # TODO: lc
    c = c / d[0]  # TODO: lc
    par['c'] = c
    w_x = []

    # Regression
    psV = []

    for i in range(0, par['ng']):  # TODO: possibly add one, needs to go to 100

        psV_x = []
        MypsV_x = np.zeros((par['ng'],len(time)))

        for j in range(0, len(time)):  # TODO: may not go to end
            psV_x.append(psiF(par['h'], par['c'], stime[j] / d[0], i))
            MypsV_x[i][j]=(psiF(par['h'], par['c'], stime[j] / d[0], i))

        psV_x = np.array(psV_x)#
        psV.append(psV_x)

        w_x.append((np.dot(sE_x[np.newaxis], np.dot(np.diag(psV_x),(ftarget[np.newaxis]).T)))/ np.dot((sE_x[np.newaxis]), np.dot(np.diag(psV_x), sE_x[np.newaxis].T)))
    
    par['psV']= np.array(psV)
    par['MypsV_x']=MypsV_x
    par['psV_x']=psV_x
    par['c'] = c
    par['stime'] = stime
    par['ftarget'] = ftarget
    par['w_x'] = np.concatenate(np.array(w_x))
    par['x0'] = x0
    par['d0'] = d[0]
    par['ctime'] = ctime

    return par

def DMP_Generate(par, target):

    f_replay_x = []
    fr_x_zeros = []

    # TODO: check usage
    ydd_x_r = 0
    yd_x_r = 0
    y_x_r = par['x0']

    ydd_xr = []
    yd_xr = []
    y_xr = []

    for j in range(0, len(par['time'])):  # TODO check if reaches end
        psum_x = 0
        pdiv_x = 0
        for i in range(0, par['ng']):  # TODO check what i is doing in psiF below
            psum_x += psiF(par['h'], par['c'], par['stime'][j] / par['d0'], i) * par['w_x'][i]
            pdiv_x += psiF(par['h'], par['c'], par['stime'][j] / par['d0'], i)

        # Generate new trajectories according to new control input
        f_replay_x.append((psum_x / pdiv_x) * par['stime'][j] * (target - par['x0']))

        if j > 0:  # TODO: check
            if np.sign(f_replay_x[j - 1]) != np.sign(f_replay_x[j]):
                fr_x_zeros.append(j - 1)  # TODO: lc

        ydd_x_r = (par['K'] * (target - y_x_r) - (par['D'] * yd_x_r) + (target - par['x0']) * f_replay_x[j]) / par['tau']
        yd_x_r = yd_x_r + (ydd_x_r * par['dt']) / par['tau']
        y_x_r = y_x_r + (yd_x_r * par['dt']) / par['tau']

        ydd_xr.append(ydd_x_r[0])
        yd_xr.append(yd_x_r[0])
        y_xr.append(y_x_r[0])

    results = {}
    results['ydd_xr'] = ydd_xr
    results['yd_xr'] = yd_xr
    results['y_xr'] = y_xr
    results['fr_x_zeros'] = fr_x_zeros
    results['f_replay_x'] = f_replay_x

    return results

# Define  new node
rospy.init_node("Sawyer_DMP")

# Create an object to interface with the arm
limb=intera_interface.Limb('right')

# Create an object to interface with the gripper
gripper = intera_interface.Gripper('right')

# Call the Sawyer Class
robot=Sawyer()

# Models location
forward_model_file='models/forwardmodel_6M.h5'
inverse_model_file='models/MLP_2.h5'

# Load the models
forwardModel= load_model(forward_model_file)
inverseModel= load_model(inverse_model_file)

# Review of the Models
#forwardModel.summary()
#inverseModel.summary()

# Move the robot to the starting point
angles=limb.joint_angles()
angles['right_j0']=math.radians(0)
angles['right_j1']=math.radians(-50)
angles['right_j2']=math.radians(0)
angles['right_j3']=math.radians(120)
angles['right_j4']=math.radians(0)
angles['right_j5']=math.radians(0)
angles['right_j6']=math.radians(0)
limb.move_to_joint_positions(angles)

#Get the initial position of the robot
joint_positions=limb.joint_angles()

#Just a vector to name the joints of the robot 
joint_names =['right_j0','right_j1','right_j3','right_j5']
full_names = ['right_j0','right_j1','right_j2','right_j3','right_j4','right_j5','right_j6']

q_init=np.array([[float(joint_positions[i]) for i in joint_names]])

# Damping factor
d=np.array([100,100,100,100]);
d_rate=1

# Initialize some counters
counter=0
error=1000
flag=1
thresh=-0.06
convert=1000000000
interp=200

# Declare the joint position history and time history
time_total=[0]
q1=[]
q2=[]
q3=[]
q4=[]


## DO FILE LOADING STUFF HERE
data = np.loadtxt('demo.txt', skiprows = 1, delimiter = ',')

# skip: 2, 4, 6
q = np.array(data[:,1:8])
target = q[-1]
time = data[:-1, 0]
q = q[:-1]
print(data.shape)
print(time.shape)
print(target.shape)
print(q.shape)


x = input("lol:")


#Interpolate all the history vectors
time = np.linspace(0, time_total[-1], interp)

#Interpolate all the joint history vectors
x=np.linspace(0,len(q1)-1,len(q1))
xvals=np.linspace(0,len(q1)-1,interp)

q1=np.interp(xvals, x, q1)
q2=np.interp(xvals, x, q2)
q3=np.interp(xvals, x, q3)
q4=np.interp(xvals, x, q4)

#Learn the DMP parameters
scale=1000
p1=DynamicMotionPrimitive(q1, time, scale)
p2=DynamicMotionPrimitive(q2, time, scale)
p3=DynamicMotionPrimitive(q3, time, scale)
p4=DynamicMotionPrimitive(q4, time, scale)


#Get the DMP results
r1=DMP_Generate(p1, p1['x'][-1])
r2=DMP_Generate(p2, p2['x'][-1])
r3=DMP_Generate(p3, p3['x'][-1])
r4=DMP_Generate(p4, p4['x'][-1])

#Plot the response of the inverse model and the learned DMP
plt.figure(1)
plt.suptitle('Joint History Response', fontsize=18)

plt.subplot(4,1,1)
plt.plot(time,q1,color='blue')
plt.plot(time,r1['y_xr'],color='red')
plt.ylabel('q1 (rad)', fontsize = 16)

plt.subplot(4, 1, 2)
plt.plot(time,q2,color='blue')
plt.plot(time,r2['y_xr'],color='red')
plt.ylabel('q2 (rad)', fontsize = 16)

plt.subplot(4, 1, 3)
plt.plot(time,q3,color='blue')
plt.plot(time,r3['y_xr'],color='red')
plt.ylabel('q3 (rad)', fontsize = 16)

plt.subplot(4, 1, 4)
plt.plot(time,q4,color='blue')
plt.plot(time,r4['y_xr'],color='red')
plt.ylabel('q4 (rad)', fontsize = 16)
plt.xlabel('time (s)', fontsize = 16)

plt.show()

# Start performing RL to adapt the DMP

# First find the target in joint space
orientation=[180,0,90]

x=target[0]
y=target[1]
z=target[2]
coordinates=[x,y,z]

#Call the IK method
ik=robot.Inverse_Kinematics(coordinates,orientation)

# Move the robot again to the starting point
angles=limb.joint_angles()
angles['right_j0']=math.radians(0)
angles['right_j1']=math.radians(-50)
angles['right_j2']=math.radians(0)
angles['right_j3']=math.radians(120)
angles['right_j4']=math.radians(0)
angles['right_j5']=math.radians(0)
angles['right_j6']=math.radians(0)
limb.move_to_joint_positions(angles)

# Wait before the RL module starts
#rospy.sleep(1)

# Send to the robot
#limb.move_to_joint_positions(ik)

#Define the target joint positions
joint_target=np.array([[float(ik[i]) for i in joint_names]])
joint_target=np.array([[joint_target[0][0],joint_target[0][1],joint_target[0][2],joint_target[0][3]]])

print ik
print joint_target

# Initialize the final trajectory
counter=0
y_final=np.zeros((interp,len(joint_target[0])))

parameters=[p1,p2,p3,p4]
results=[r1,r2,r3,r4]

# Change the gain values of the RL policies
parameters[0]['K']=40000
parameters[0]['D']=100

parameters[1]['K']=60000
parameters[1]['D']=100

parameters[2]['K']=60000
parameters[2]['D']=100

parameters[3]['K']=80000
parameters[3]['D']=200

#parameters[3]['K']=4
#parameters[3]['D']=1

plotGaussians('stime', 'psV', parameters, None, "Initial Policies")
plotGaussians('stime', 'psV', parameters, np.array([p1['w_x'], p2['w_x'], p3['w_x'], p4['w_x']]), "Learned Policies")
w_all = []

for i in range(0, len(joint_target[0])):

    print '--------------------'

    w_a=parameters[i]['w_x']
    print(w_a.shape)
    # Initialize the action array
    flag=0
    samples=10
    sampling_rate=0.5

    actions=np.zeros((parameters[i]['ng'],samples))

    while flag==0:

        # Set the exploration parameters
        expl=np.zeros((parameters[i]['ng'],samples))

        for z in range(0,samples):
            for j in range(0,parameters[i]['ng']):
                expl[j][z]=np.random.normal(0,np.std(parameters[i]['MypsV_x'][:,j]*w_a)) #parameters[i]['w_x'][j]))
                
        # Sample all the possible actions
        actions = np.add(expl, w_a)

        # Generate new rollouts
        f_replay_x = np.zeros((samples,len(parameters[i]['time'])))

        ydd_xr = np.zeros((samples,len(parameters[i]['time'])))
        yd_xr = np.zeros((samples,len(parameters[i]['time'])))
        y_xr = np.zeros((samples,len(parameters[i]['time'])))

        for g in range(0,samples):

            ydd_x_r=0;
            yd_x_r=0;
            y_x_r=parameters[i]['x'][0];

            for j in range(0, len(parameters[i]['time'])):
                psum_x = 0
                pdiv_x = 0
        
                for z in range(0, parameters[i]['ng']):
                    psum_x += psiF(parameters[i]['h'], parameters[i]['c'], parameters[i]['stime'][j] / parameters[i]['d0'], z) * actions[z,g]
                    pdiv_x += psiF(parameters[i]['h'], parameters[i]['c'], parameters[i]['stime'][j] / parameters[i]['d0'], z)

                # Generate new trajectories according to new control input
                f_replay_x[g][j]=(psum_x / pdiv_x) * parameters[i]['stime'][j] * (joint_target[0][i]- parameters[i]['x0'])
                ydd_x_r = (parameters[i]['K'] * (joint_target[0][i] - y_x_r) - (parameters[i]['D'] * yd_x_r) + (joint_target[0][i] - parameters[i]['x0']) * f_replay_x[g][j]) / parameters[i]['tau']
                yd_x_r = yd_x_r + (ydd_x_r * parameters[i]['dt']) / parameters[i]['tau']
                y_x_r = y_x_r + (yd_x_r * parameters[i]['dt']) / parameters[i]['tau']

                ydd_xr[g][j]=ydd_x_r;
                yd_xr[g][j]=yd_x_r;
                y_xr[g][j]=y_x_r;
       

        #Estimate the Q values
        Q=np.zeros((1,samples))
        for z in range(0,samples):
            Q_sum=0
            for j in range(0, len(parameters[i]['time'])):

                Q_sum=Q_sum+MyReward(joint_target[0][i],y_xr[z][j],parameters[i]['time'][j],parameters[i]['tau'])

            Q[0][z]=Q_sum;

        # Sample the highest Q values to aptade the action parameters
        high=np.floor(sampling_rate*samples);
        Q_sort=-np.sort(-Q)
        I = np.argsort(Q);


        # Update the action parameters
        sumQ=0;
        sumQ_up=0;

        for j in range(0,int(high)):

            sumQ += Q_sort[0][j];
            sumQ_up += expl[:,I[0][j]]*Q_sort[0][j];


        summary=sumQ_up/sumQ

        w_a -= summary.reshape(parameters[i]['ng'],1);

        counter=counter+1;

        print y_xr[I[0][1]][-1]
        if(math.fabs(y_xr[I[0][1]][-1]-joint_target[0][i])<0.01):
            flag=1;
            y_final[:,i]=y_xr[I[0][1]][:]

    w_all.append(w_a)

plotGaussians('stime', 'psV', parameters, np.array(w_all), "Modified Policies")

init_w = np.array([p1['w_x'], p2['w_x'], p3['w_x'], p4['w_x']])

final = init_w - np.array(w_all)

plotGaussians('stime', 'psV', parameters, final, "Totals")
print(final)



plt.figure(2)
plt.subplot(4,1,1)
plt.plot(time,q1,color='blue', label='NN')
plt.plot(time,r1['y_xr'],color='red', label='DMP')
plt.plot(time,y_final[:,0],color='black', label='RL')
plt.title('Joint History Response', fontsize = 18)
plt.legend(loc="upper right")
plt.ylabel('q1 (rad)', fontsize = 16)

plt.subplot(4, 1, 2)
plt.plot(time,q2,color='blue', label='NN')
plt.plot(time,r2['y_xr'],color='red', label='DMP')
plt.plot(time,y_final[:,1],color='black', label='RL')
plt.ylabel('q2 (rad)', fontsize = 16)

plt.subplot(4, 1, 3)
plt.plot(time,q3,color='blue', label='NN')
plt.plot(time,r3['y_xr'],color='red', label='DMP')
plt.plot(time,y_final[:,2],color='black', label='RL')
plt.ylabel('q3 (rad)', fontsize = 16)

plt.subplot(4, 1, 4)
plt.plot(time,q4,color='blue', label='NN')
plt.plot(time,r4['y_xr'],color='red', label='DMP')
plt.plot(time,y_final[:,3],color='black', label='RL')
plt.ylabel('q4 (rad)', fontsize = 16)
plt.xlabel('time (s)', fontsize = 16)

plt.show()

jnots = np.zeros((interp, 1))
traj_final = np.concatenate(((y_final[:,0:2], jnots, y_final[:,2][:, np.newaxis], jnots, y_final[:,3][:, np.newaxis], jnots)), axis=1)
traj_final = np.concatenate((traj_final,np.multiply(np.ones((interp,1)),0.0402075604203)),axis=1)

# Create the trajectory file 
max_time=parameters[0]['time'][-1]
time=np.linspace(0, max_time,interp).reshape((interp,1))

traj_final=np.concatenate((time,traj_final),axis=1)

# Save trajectory
np.savetxt('traj_final.txt', traj_final, delimiter=',',header='time,right_j0,right_j1,right_j2,right_j3,right_j4,right_j5,right_j6,right_gripper',comments='',fmt="%1.12f")

