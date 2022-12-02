import numpy as np
import matplotlib.pyplot as plt
import cv2
import copy
import random

from tqdm import tqdm

import os

class Inpainter:
    def __init__(self, img:np.ndarray, mask:np.ndarray, patch_size = 9, show = False):

        self.img = img
        self.mask = mask
        self.h, self.w = mask.shape[0], mask.shape[1]
        self.patch_size = patch_size
        # all one in the fill_range
        self.fill_range = copy.deepcopy(mask)

        # ##################
        self.fill_image = np.where(np.dstack([self.mask, self.mask, self.mask])==1, np.zeros(shape=img.shape, dtype="uint8"), img)
        # the fill front
        self.fill_front = self.update_contours()
        # C(p) and D(p) in the paper
        self.confidence = (self.mask==0).astype("float")
        self.data = np.zeros(shape=mask.shape)
        self.image_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self.priority_q = None

        self.show = show

    def update_contours(self):
        """
        return the edge contours np.ndarray(point number, 1)
        """
        contours, hierarchy = cv2.findContours(self.fill_range, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        # print(len(contours[0]))
        self.fill_front = contours[0].reshape(-1, 2)[:, ::-1]
        if len(contours) > 1:

            for i in range(1, len(contours)):
                self.fill_front = np.concatenate((self.fill_front, contours[i].reshape(-1, 2)[:,::-1]), axis=0)


    def get_data(self, img, patch:np.ndarray):
        """
        input the patch(2x2) return the image data in the patch
        patch[0, 0]---------patch[1, 0]
        |                   |
        |                   |
        |                   |
        patch[0, 1]---------patch[1, 0]
        """
        return img[patch[0, 0]:patch[0, 1], patch[1, 0]:patch[1, 1]]

    def get_patch(self,point):
        """
        given a point return a patch of size patch_size x patch_size
        """
        k = self.patch_size // 2
        # consider the domain edge
        patch_range = [[max(0, point[0]-k), min(point[0]+k+1, self.h)], 
                       [max(0, point[1]-k), min(point[1]+k+1, self.w)]]
        return np.array(patch_range)

    def update_C(self):
        for point in self.fill_range:
            patch = self.get_patch(point)
            confidence_sum = self.get_data(self.confidence, patch).sum()
            # Because the domain edge issue, we cannot direct get patch_size x patch_size as a area
            area = (patch[0, 1]-patch[0, 0])*(patch[1, 1]-patch[1, 0])
            self.confidence[point[0], point[1]] = confidence_sum/area

    def get_norm(self):
        """
        get x, y direction derivative, normalize the vector(g_x, g_y)
        """
        g_x = cv2.Scharr(self.fill_range, cv2.CV_64F, 1, 0)
        g_y = cv2.Scharr(self.fill_range, cv2.CV_64F, 0, 1)
        # stack g_x[NxM] and g_y[NxM] -> [N, M, 2]
        normal = np.dstack([g_y, g_x])
        # print(normal)
        norm = np.sqrt(g_x * g_x + g_y * g_y).reshape(self.h, self.w, 1).repeat(2, axis=2)
        norm[norm == 0] = 1
        unit_normal = normal/norm
        return unit_normal

    def get_isophote(self):
        # get masked image
        masked_img = np.ma.masked_array(self.image_gray, mask=self.fill_range)
        g_x, g_y = np.gradient(masked_img)
        g_y = np.ma.filled(g_y, 0)
        g_x = np.ma.filled(g_x, 0)
        gradient = np.sqrt(g_x*g_x + g_y*g_y)
        # normalized the gradient
        gradient = gradient * (255/gradient.max())

        isophote = np.zeros((self.h, self.w, 2))
        for point in self.fill_front:
            patch = self.get_patch(point)
            g_y_patch = self.get_data(g_y, patch)
            g_x_patch = self.get_data(g_x, patch)

            grad_patch = self.get_data(gradient, patch)

            # find the index of max scale of gradient in the patch region
            max_patch_pos = np.unravel_index(grad_patch.argmax(), grad_patch.shape)

            isophote[point[0], point[1], 0] = g_x_patch[max_patch_pos]
            # minus is important here
            isophote[point[0], point[1], 1] = -g_y_patch[max_patch_pos]

        return isophote

    def update_D(self, a=255):
        n_vec = self.get_norm()    
        isophote = self.get_isophote()     
        self.data = np.sqrt(np.sum((n_vec*isophote)**2, axis=2))/a

    def update_prioity(self):
        self.update_C()
        self.update_D()
        # set 0 in the already filled region, update the priority in the unfilled region
        priority = self.confidence*self.data*self.fill_range
        self.priority_q = np.array([priority[pt[0], pt[1]] for pt in self.fill_front])

    def get_patch_distance(self, target_patch, dst_patch):

        return (((dst_patch[:, 0] - target_patch[:, 0])**2).sum())**0.5

    # def get_patch_difference(self, target_point, dst_patch):
    #     pass

    def find_best_match(self, target_point):
        """
        fin the best matching point in the image 
        """
        target_patch = self.get_patch(target_point)
        target_patch_img = self.get_data(self.fill_image, target_patch)
        
        patch_h = target_patch[0, 1]- target_patch[0, 0]
        patch_w = target_patch[1, 1]- target_patch[1, 0]

        best_patch = None
        least_dis = float("inf")

        for i in tqdm(range(self.h-patch_h+1)):
            for j in range(self.w-patch_w+1):
                dst_patch = np.array([[i, i+patch_h], [j, j+patch_w]])

                # check if the destination patch is filled
                if(self.get_data(self.fill_range, dst_patch).sum() == 0):
                    # patch_diff = self.get_patch_difference(target_patch, dst_patch)
                    patch_dist = self.get_patch_distance(target_patch, dst_patch)
                    if least_dis > patch_dist:
                        least_dis = patch_dist
                        best_patch = dst_patch
            
        # best_img = self.get_data(self.img, best_patch)
        return best_patch

    def aprox_best_match(self, target_point, step = 1000):
        """
        find an approximate match within limited step
        """
        target_patch = self.get_patch(target_point)
        target_patch_img = self.get_data(self.fill_image, target_patch)
        plt.subplot(121), plt.imshow(target_patch_img)

        patch_h = target_patch[0, 1]- target_patch[0, 0]
        patch_w = target_patch[1, 1]- target_patch[1, 0]

        Area = (self.h-patch_h)*(self.w-patch_w)

        if step > Area:
            step = Area/2

        row_n = np.floor((self.h/(np.sqrt(Area/step))))
        col_n = np.floor((self.w/(np.sqrt(Area/step))))

        rand_rows = random.sample(range(self.h-patch_h+1), int(row_n))
        rand_cols = random.sample(range(self.w-patch_w+1), int(col_n))

        # print("step = {}".format(len(rand_rows)*len(rand_cols)))

        best_patch = None
        least_dis = float("inf")

        for i in rand_rows:
            for j in rand_cols:
                dst_patch = np.array([[i, i+patch_h], [j, j+patch_w]])

                # check if the destination patch is filled
                if(self.get_data(self.fill_range, dst_patch).sum() == 0):
                    # patch_diff = self.get_patch_difference(target_patch, dst_patch)
                    patch_dist = self.get_patch_distance(target_patch, dst_patch)
                    if least_dis > patch_dist:
                        least_dis = patch_dist
                        best_patch = dst_patch
            
        # best_img = self.get_data(self.img, best_patch)
        return best_patch

    def fill_patch(self, target_point:np.ndarray, approx = False, step = 1000):
        """
        fill the patch centered at the point, and update all properties
        """
        target_patch = self.get_patch(target_point)
        target_patch_mask = self.get_data(self.fill_range, target_patch)
        target_data = self.get_data(self.fill_image, target_patch)
        if(self.show == True):
            plt.subplot(221), plt.imshow(target_data)
        # get the coordinate of the position to be fill
        coord_to_fill = np.where(target_patch_mask == 1)
        # print("coord_to_fill :")
        # print(coord_to_fill[0])
        # print(coord_to_fill[1])
        
        if approx == True:
            dst_patch = self.aprox_best_match(target_point)
        else:
            dst_patch = self.find_best_match(target_point)

        # plt.subplot(131), plt.imshow(target_data), plt.title("image to fill")
        # plt.subplot(132), plt.imshow(self.get_data(self.img, dst_patch)), plt.title("Global search")
        # plt.subplot(133), plt.imshow(self.get_data(self.img, apx_patch)), plt.title("Approximate search")
        # plt.show()
        dst_data = self.get_data(self.fill_image, dst_patch)

        # fill the blank region
        target_data[coord_to_fill[0], coord_to_fill[1]] = dst_data[coord_to_fill[0], coord_to_fill[1]]

        # update the confidence in target patch
        target_confidence = self.get_data(self.confidence, target_patch)
        target_confidence[coord_to_fill[0], coord_to_fill[1]] = self.confidence[target_point[0], target_point[1]]
        
        # print(type(target_confidence), target_confidence.shape)

        # update the fill_range
        target_fill_range = self.get_data(self.fill_range, target_patch)
        target_fill_range[coord_to_fill[0], coord_to_fill[1]] = 0

        # update the gray_img
        self.image_gray = cv2.cvtColor(self.fill_image, cv2.COLOR_BGR2GRAY)

        if(self.show == True):
            plt.subplot(222), plt.imshow(dst_data)
            plt.subplot(223), plt.imshow(target_data)
            plt.subplot(224), plt.imshow(target_fill_range)
            plt.show()


    def exe_inpaint(self, filename:str, approx = False, step = 1000):
        while self.fill_range.sum() > 0:
            self.update_contours()
            self.update_prioity()
            # find the point with max priority
            target_point = self.fill_front[self.priority_q.argmax()]
            # plt.subplot(121), plt.imshow(self.fill_image)
            print("{} points left to fill ".format(self.fill_range.sum()))
            print("target_point = ", target_point)
            self.fill_patch(target_point, approx, step)

            # plt.subplot(122), plt.imshow(self.fill_image)
            # plt.show()
        
        # save
        cv2.imwrite(filename, self.fill_image)


if __name__ == "__main__":
    img_src = "D:\Courses_2022_Fall\ECE4513\Projects\src\MyCode\mask_color.jpg"
    mask_src = "D:\Courses_2022_Fall\ECE4513\Projects\src\MyCode\mask_black.jpg"
    patch_size = 9

    img = cv2.imread(img_src)
    mask = cv2.imread(mask_src, 0)

    inpainter = Inpainter(img, mask, patch_size)
    # inpainter.exe_inpaint("approx1000.jpg", approx=True, step = 1000)
    # inpainter.exe_inpaint("approx10000.jpg", approx=True, step = 10000)
    inpainter.exe_inpaint("output.jpg")